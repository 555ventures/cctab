# Canonical: daily-cost-view

Per-model, per-day cost reporting for cctab. Landed by `specs/20260616/01-daily-cost-by-model.md`;
narrowed to a single cwd-scoped screen by `specs/20260616/02-cwd-only-daily.md`.
Cost is **per-model accurate everywhere** — the daily view routes dollars through `cost_of`,
never a single blended rate.

## Model-family rate table (`data.py`)

Rates and the margin are the **only** numeric knobs and live exclusively in `src/cctab/data.py`
(Worker Rule: number/cost discipline — never a `$/MTok` literal elsewhere).

- `RATES: dict[str, ModelRate]` keyed by canonical lowercase family. `ModelRate` is a frozen
  dataclass of `input`/`output`/`cache_write`/`cache_read` `$/MTok` rates.
- Each class is env-overridable per family via `CCTAB_RATE_<FAMILY>_<CLASS>` (e.g.
  `CCTAB_RATE_OPUS_INPUT`), resolved by the `_rate()` helper at module load.
- Families: `haiku`, `sonnet`, `opus`, `fable` (the displayed columns, in that left→right order
  via the `FAMILIES` tuple), plus two internal families with no column — `synthetic` (priced at
  **$0**) and `default` (unknown models, seeded from the legacy blended `RATE_*` constants so
  `Usage.cost` and AC-DATA-2 stay intact).
- `fable` is seeded at opus-tier rates as a visible placeholder (no public list price known at
  plan time); override via `CCTAB_RATE_FABLE_*` or update the seed when known.

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

## client margin (`client_cost` / per-directory `.cctab`)

`CLIENT $ = cost × MARGIN`, a **markup multiplier**, default `1.0` (client == cost).
`MARGIN` lives in `data.py`; `client_cost(cost)` is the single function that applies it — reuse it
everywhere (per-row cells via `DayUsage.client`, and summary totals), never an inline `× margin`.
The active margin **is** the module global `data.MARGIN`; `set_margin(value)` mutates it via
`globals()["MARGIN"] = value` (a `from`-import rebind would not update the attribute) and
`current_margin()` reads it back.

### Per-directory margin (`.cctab`)

The client markup is **per-directory**, stored in `<launch dir>/.cctab` as JSON `{"margin":
<number>}` (filename constant `DIR_CONFIG_NAME` in `data.py`; only `margin` is read, extra keys
ignored). cctab reads it on launch with precedence **`.cctab` → `CCTAB_MARGIN` env → `1.0`**, and
writes it when the user edits the margin in-app with `e` on the daily view (`escape` cancels —
an empty Input with the current margin shown as a placeholder hint). The file is created **only
on edit** (never just by viewing a directory), read **best-effort** (`read_dir_margin` returns
`None` for a missing, unparseable, non-numeric, bool, string, negative, or non-finite value — so
it falls back silently and never poisons CLIENT $), and written **atomically** (`write_dir_margin`
uses a same-dir `tempfile` + `os.replace`, swallows `OSError`, cleans up its temp, returns `False`
on failure — a read-only dir degrades, never crashes; a failed write surfaces `(could not write
.cctab)` in the summary label). Model `$/MTok` rates remain global in `data.py`'s `RATES`; only
the margin is per-directory. Suggest adding `.cctab` to `.gitignore`. This is cctab's only disk
write — every other surface is read-only.

## cwd scope (`cwd_in_scope`)

A session matches a scope when its transcript `cwd` **is the scope directory or nested beneath
it** (`cwd_in_scope(session_cwd, scope)`), so launching in a project folds in its
`.claude/worktrees/*` sessions; `scope=None` → global (everything matches). The app always passes
the launch cwd to `scan_daily(cwd=…)`; the data-layer global path (`scope=None`) is retained for
tests and library callers but is no longer reachable from the TUI.

## transcript discovery (`_iter_files`)

Transcripts are **not** "one `*.jsonl` per session at one level". Claude Code also writes
**nested** subagent/workflow transcripts at depth ≥3 — `<encoded-cwd>/<session>/subagents/…`,
and deeper `subagents/workflows/wf_*/agent-*.jsonl` — each carrying its own internal `cwd`. On a
real corpus 59 % of all token usage lived at depth ≥3, so a one-level glob silently dropped most
delegated (subagent/workflow) usage. Discovery is therefore **recursive and deterministic**:
`_iter_files(projects_dir)` yields every `*.jsonl` at any depth via `rglob("*.jsonl")`, **sorted
by path string** (the sort makes first-occurrence-wins dedup reproducible run-to-run). Nested
transcripts are first-class and attributed by **their own internal `cwd`**, so `cwd_in_scope`
folds workflow/subagent usage into the launch directory exactly like top-level sessions; no
attribution change was needed. `scan_daily` resolves a file's scope cheaply via `_first_cwd(path)`
(first line's `cwd`, falling back to `path.parent.name`) and **skips out-of-scope files entirely
before they can consume a dedup id** (see below). Non-transcript nested files (`journal.jsonl`,
etc.) carry no `message.usage`, so they parse to empty Sessions — harmless under best-effort
parsing. Discovery grew from ~559 to ~2,857 files on a real corpus; the existing
`progress(done, total)` callback already covers the larger count.

## per-message dedup (`seen_ids`)

A single assistant message is written as **multiple JSONL lines sharing one `message.id`**, each
repeating the *identical* `message.usage` block (one line per content block) — and resumed
sessions re-log prior messages **across files** too. Counting every line over-counts ~2.5×. cctab
counts each `message.id` **once per scan run** (a `seen_ids: set[str]` threaded through
`_parse_file`): the first occurrence in deterministic path-then-line order is counted, later
occurrences contribute nothing (differing-usage ids — ~2.6 %, minor cache-read deltas — take the
first occurrence, not a max). A line **lacking** a string `message.id` is always counted and never
merged (legacy/synthetic transcripts). `_parse_file(path, seen_ids=None)` defaults to a fresh
per-call set (per-file dedup) for standalone/library use; `scan` threads **one** set across all
files. `scan_daily(cwd=X)` must scope-filter **before** a file consumes an id — a global set
marked while parsing *every* file would let an out-of-scope duplicate suppress the in-scope
occurrence — so it skips out-of-scope files without touching `seen_ids`, making the in-scope
result independent of file order. This per-message dedup plus full nested discovery is what makes
token totals — and therefore `EST $` and `CLIENT $` — a defensible billing basis rather than a
~2.5× over-count.

## single-screen structure (Textual `MODES`)

`CCTab` is the **daily view only**, always scoped to the launch directory. `MODES = {"daily":
DailyScreen}` with `DEFAULT_MODE = "daily"` is kept as a single-entry map to preserve Textual's
auto-mount boot (deleting `MODES` would need a `push_screen`/`SCREENS` restructure). The
app-level key map is `q` (quit) and `r` (refresh); there is no mode switch, no global-scope
toggle, no `--global` flag, no leaderboard, and no session drill-down — those surfaces were
removed in `02`. The launch cwd is captured **once at construction** (`self._launch_cwd`, settable
via the `CCTab(launch_cwd=…)` param for tests) and never re-read on rescans. A threaded `@work`
scan pass calls `scan_daily(cwd=self._launch_cwd)` and re-renders `DailyScreen` via `_on_loaded`.
`DailyScreen` is day-rows × family-columns with `$cost(tokens)` cells (via `model_cell`) and
`EST $`/`CLIENT $` totals.

## Billing export (`space` / `y`, `daily_csv`)

The daily view supports a hand-rolled multi-select (Textual `DataTable` has no native multi-row
selection). `DailyScreen.selected` is a `set[str]` of day keys initialized in `__init__`; rows
are added keyed by `d.day` with a leftmost 2-wide `mark` column showing `●` for marked days.
`space` (`action_toggle_select`) toggles the cursor row's day (`app.days[table.cursor_row].day`);
`refresh_daily` prunes `selected` to present days first, so a day gone after a rescan never rides
into a bill. `y` (`action_copy_csv`) copies the marked days — or the whole visible table when none
are marked — to the system clipboard, confirmed with a transient `App.notify` toast (never
clobbering the summary). Copy uses **two paths**: Textual's `App.copy_to_clipboard` (an OSC 52
escape sequence) **and** a best-effort native write via `system_clipboard_copy` — many terminals
(Terminal.app, tmux without `set-clipboard on`) silently drop OSC 52, so the keypress would
otherwise appear to work while the clipboard stays empty. `system_clipboard_copy` shells out to
the platform clipboard tool (`pbcopy` on darwin, `clip` on win32, `wl-copy`/`xclip`/`xsel` on
others), best-effort: a missing tool, non-zero exit, or `OSError` returns `False` and the toast
reads "sent … to terminal clipboard — paste to check" instead of "copied". It never raises and
never writes to stdout (the TUI owns the screen). This subprocess call and the `.cctab` write are
cctab's **only** two surfaces that aren't read-only. Serialization is the pure module-level
`daily_csv(days) -> str` in `app.py` (stdlib `csv`): a header, one raw row per day
(`day`, per-`FAMILIES` `<fam>_tokens`/`<fam>_cost`, `est_usd`, `client_usd`), and a `TOTAL` row —
tokens as integers, dollars as 2-decimal floats, no `$` or `human()` abbreviation, so every
column is spreadsheet-`SUM()`-able. A family absent from a day contributes `0`/`0.00` (never
`cost_of(None, …)`, which would raise); per-family dollars route through `data.cost_of` (no rate
literal in `app.py`).
