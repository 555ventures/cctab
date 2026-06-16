---
date: 2026-06-16
status: done
risk: T2
area: daily-cost-view
design: false
breaking: false
depends_on: []
depended_on_by: []
---

# Daily token & cost view, per-model, cwd-scoped, with client margin

## Goal

Turn cctop's default screen into a **daily breakdown for the current directory**: one row per
day, a column per model family showing `$cost(tokens)`, plus per-day `EST $` (your cost) and
`CLIENT $` (cost × margin) totals. Launching in a directory shows only that directory's days;
`--global` widens to all directories. Cost becomes **per-model accurate** (opus, sonnet, fable
and haiku each priced from their own $/MTok rates) instead of one blended rate, and a
`CCTOP_MARGIN` markup multiplier yields a client-facing cost column. The existing
per-directory leaderboard is preserved as a second, switchable view. "Done" = `cctop` opens on
the cwd daily view, `cctop --global` on the all-dirs daily view, both numbers per-model
accurate, and `p`/`d` switch between daily and the leaderboard.

## Decisions (locked — workers apply verbatim, never override)

| ID | Decision | One-line rationale |
|----|----------|--------------------|
| D1 | Default view = **daily**, scoped to the launch cwd; `--global` flag widens scope to all directories. | Matches the request ("cost of current dir unless I pass global flag"); rejected keeping the leaderboard as the default. |
| D2 | Daily layout = one row per **day**; a fixed column per model family (Haiku, Sonnet, Opus, Fable) whose cell renders `$cost(tokens)`; trailing `EST $` and `CLIENT $` are the day's totals across **all** models. | User-confirmed layout; rejected one-row-per-(day,model) and tokens-only cells. |
| D3 | Per-model **rate table** `RATES` in `data.py`, keyed by family, seeded with public-list $/MTok, each class env-overridable per family; unknown model → `default` family (today's blended rates); `<synthetic>` → `synthetic` family priced at $0. | "Cost incurred" by model is only meaningful with per-model rates; rejected the single blended rate. |
| D4 | `CLIENT $` = `cost × CCTOP_MARGIN`, a **markup multiplier**, default `1.0` (client = cost). | User chose the multiplier convention; rejected percentage and gross-margin conventions. |
| D5 | Days are bucketed by **local calendar day** derived from each line's UTC `timestamp`. | Matches a user's wall-clock workday; rejected UTC-day bucketing. `2026-06-16T02:30:00Z` under `TZ=UTC` → day `2026-06-16`. |
| D6 | Two top-level views are Textual **App `MODES`** — `daily` (`DEFAULT_MODE`) and `projects` (the existing leaderboard moved verbatim into a `Screen`). `d`/`p` switch modes; `g` toggles cwd/global scope at runtime for the active view. | Independent navigable stacks per the Textual modes idiom; rejected pushing daily as a modal over the leaderboard. |
| D7 | Model family resolved by **case-insensitive substring** match on the model id (`opus`/`sonnet`/`haiku`/`fable`), `<synthetic>` → `synthetic`, else `default`. | Survives version bumps (`claude-opus-4-8`, future `-4-9`); rejected exact-id mapping. |
| D8 | The daily view filters out `<synthetic>`-only contribution from the four model columns, but `EST $`/`CLIENT $`/day-total **include every** family (any non-columned family rolls into the totals; synthetic adds $0). | Honest totals; columns are a breakdown, not the source of the total. |

## File Plan

| Path | Action | Layer | Summary |
|------|--------|-------|---------|
| `src/cctop/data.py` | MODIFY | data | `ModelRate`/`RATES` table + per-family env overrides; `family_of()`, `cost_of(usage, family)`, `MARGIN`/`client_cost()`; `_local_day()` (Z-safe); extend `_parse_file` to bucket `(day, family) → Usage`; add `Session.by_model`, `Project.by_model`, `Project.cost`; add `DayUsage` + `scan_daily(projects_dir, cwd)`. Keep `Usage.cost`, `RATE_*`, `scan()` merge behavior intact. |
| `src/cctop/app.py` | MODIFY | tui | Restructure into `MODES = {"daily": DailyScreen, "projects": ProjectsScreen}`, `DEFAULT_MODE="daily"`; move existing leaderboard into `ProjectsScreen` (cost accessors swapped to accurate `Project.cost`/`Session.cost`); new `DailyScreen` (day rows × model columns); `model_cell()` helper; `CCTop` holds `scope_cwd` + threaded scan shared by both screens; `parse_scope(argv)` + `main()` reads `sys.argv`; `action_refresh` rescans + re-renders the **active** screen; `action_toggle_scope` + mode-switch bindings; client cost via `cost_cell`. |
| `tests/test_data.py` | MODIFY | tests | AC-DATA-6, AC-DATA-7, AC-DATA-8, AC-DATA-9, AC-DATA-10, AC-DATA-11, AC-DATA-12 (extend `_write_session` to emit `timestamp`/`model` per row) |
| `tests/test_app.py` | MODIFY | tests | AC-APP-1 (update: enter projects mode before leaderboard keys), AC-APP-2, AC-APP-3, AC-APP-4 |
| `README.md` | MODIFY | other | Daily-default + `--global`, `CCTOP_MARGIN`, per-family `CCTOP_RATE_*` env vars, updated keys table, two-view note. |

## Contracts

All in `src/cctop/data.py`. Rates and the margin are the **only** new numeric knobs and live
here exclusively (Worker Rule: number/cost discipline).

```python
# Family keys (canonical, lowercase)
FAMILIES = ("haiku", "sonnet", "opus", "fable")   # display/column order, left→right
# plus internal: "synthetic" (zero cost, no column), "default" (unknown models)

@dataclass(frozen=True)
class ModelRate:
    input: float
    output: float
    cache_write: float
    cache_read: float

def _rate(family: str, defaults: ModelRate) -> ModelRate:
    """Per-family rate, each class overridable via CCTOP_RATE_<FAMILY>_<CLASS>."""
    g = lambda cls, d: float(os.environ.get(f"CCTOP_RATE_{family.upper()}_{cls}", d))
    return ModelRate(
        input=g("INPUT", defaults.input),
        output=g("OUTPUT", defaults.output),
        cache_write=g("CACHE_WRITE", defaults.cache_write),
        cache_read=g("CACHE_READ", defaults.cache_read),
    )

# Default family == today's blended rates (keeps RATE_* + existing Usage.cost intact).
RATES: dict[str, ModelRate] = {
    "opus":      _rate("OPUS",   ModelRate(15.0, 75.0, 18.75, 1.50)),
    "sonnet":    _rate("SONNET", ModelRate(3.0,  15.0, 3.75,  0.30)),
    "haiku":     _rate("HAIKU",  ModelRate(1.0,  5.0,  1.25,  0.10)),
    "fable":     _rate("FABLE",  ModelRate(15.0, 75.0, 18.75, 1.50)),  # placeholder; see A1
    "synthetic": ModelRate(0.0, 0.0, 0.0, 0.0),
    "default":   ModelRate(RATE_INPUT, RATE_OUTPUT, RATE_CACHE_WRITE, RATE_CACHE_READ),
}

MARGIN = float(os.environ.get("CCTOP_MARGIN", "1.0"))

def family_of(model: str | None) -> str: ...        # substring match, D7
def cost_of(usage: Usage, family: str) -> float: ... # usage × RATES[family], / 1e6
def client_cost(cost: float) -> float:               # cost * MARGIN
    return cost * MARGIN
def _local_day(timestamp: str | None) -> str:        # ISO→local YYYY-MM-DD, "unknown" on fail

@dataclass
class DayUsage:
    day: str                              # "YYYY-MM-DD" or "unknown"
    by_model: dict[str, Usage]            # family → summed Usage (only families that appear)
    @property
    def total(self) -> int: ...           # Σ usage.total over all families
    @property
    def cost(self) -> float: ...          # Σ cost_of(u, fam) over all families
    @property
    def client(self) -> float:            # client_cost(self.cost)
        return client_cost(self.cost)

# Session/Project gain a per-family rollup; both gain accurate cost.
# Session.by_model: dict[str, Usage] = field(default_factory=dict)   # MUST follow no-default fields
# Project.by_model: dict[str, Usage] = field(default_factory=dict)   # Project already uses field(); mirror it
# Session.cost -> float == Σ cost_of(u, fam) for fam, u in by_model.items()
# Project.cost -> float == Σ cost_of(u, fam) for fam, u in by_model.items()

def scan_daily(
    projects_dir: Path | None = None,
    cwd: str | None = None,               # None → all dirs (global); else only sessions whose cwd == this
    progress: Callable[[int, int], None] | None = None,
) -> list[DayUsage]:
    ...
# Sort: real days descending, "unknown" always last. A bare reverse=True is WRONG
# ("u" > "2" in ASCII puts "unknown" first), and a tuple key with global reverse=True
# also flips "unknown" to the front. Use the explicit two-list form:
#   real = sorted((d for d in days if d.day != "unknown"), key=lambda d: d.day, reverse=True)
#   unknown = [d for d in days if d.day == "unknown"]
#   return real + unknown
```

`Usage` stays a **token-only** bucket. `Usage.cost` (blended, `default`-family) is retained
unchanged for backward compatibility and the `AC-DATA-2` test. `_parse_file` now also records,
per usage-bearing line, `(_local_day(line["timestamp"]), family_of(line["message"]["model"]))
→ Usage`, accumulated onto the session; it must still **never raise** on missing
`timestamp`/`model`/`usage` or malformed JSON (best-effort guarantee preserved).

## UI

Textual **modes** (resolved via Context7 `/websites/textual_textualize_io`, *Guide → Screens →
Modes*). Build workers use exactly this shape and do not query MCP:

```python
class CCTop(App):
    MODES = {"daily": DailyScreen, "projects": ProjectsScreen}
    DEFAULT_MODE = "daily"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("g", "toggle_scope", "cwd/global"),
        Binding("d", "switch_mode('daily')", "Daily"),
        Binding("p", "switch_mode('projects')", "Projects"),
    ]
    # holds shared state: scope_cwd: str|None, projects: list[Project], days: list[DayUsage]
    # threaded scan (@work) populates both; each screen renders from app state on activation.
    def action_toggle_scope(self) -> None: ...   # flips scope_cwd between launch cwd and None; rescans
```

- **`DailyScreen(Screen)`** — DEFAULT. Owns its own `DataTable` (independent of the
  leaderboard's `COLUMNS`/`SORTABLE`): columns `DAY`, then one per family in `FAMILIES` order
  (`HAIKU`, `SONNET`, `OPUS`, `FABLE`), then `EST $`, `CLIENT $`. Rows = `app.days` (already
  day-desc). Each model cell = `model_cell(usage, family)` rendering `f"${cost:,.2f}({human(tok)})"`,
  dim when zero; `EST $`/`CLIENT $` via existing `cost_cell`. Summary bar (`#summary`) shows
  scope (`cwd: ~/p` vs `global`), day count, total tokens, total `EST $`, total `CLIENT $`,
  and the active `CCTOP_MARGIN`. **Empty state:** when `app.days` is empty, the table is empty
  and the summary reads e.g. `no transcripts for ~/p — press g for global`. **Loading:**
  summary shows `scanning…` (today's pattern).
- **`ProjectsScreen(Screen)`** — the current leaderboard moved into a `Screen`: existing
  `COLUMNS`, `SORTABLE`, `refresh_table`, the `t`/`c`/`o`/`n`/`m`/`/`/`esc` bindings and
  `action_*`, and the `enter`→`SessionsScreen` drill move with it. **Two reconciled deltas vs
  the old code:** (1) its row set is filtered by `app.scope_cwd` (cwd → only that project;
  global → all); (2) the cost column/sort read the accurate `Project.cost` (not
  `p.usage.cost`) — `SORTABLE["cost"]` becomes `lambda p: p.cost`. The `#summary` Static lives
  on this screen (not on `CCTop`); `action_refresh` on `CCTop` triggers a rescan and the
  active screen re-renders its own summary/table.
- **`SessionsScreen`** — structurally unchanged, but its `EST $` cells read `s.cost` (the
  per-family-accurate `Session.cost`) instead of `s.usage.cost`, and the title total uses
  `proj.cost`. This is the single intentional edit to the drill-down.
- `model_cell(usage, family)` is a new render helper in `app.py` alongside `human`/`num_cell`/
  `cost_cell`; it must reuse `human` for the token part and `data.cost_of` for the dollar part
  — no rate literal, no re-derived formatting.

## Data Model

No persistence. All aggregation stays in-memory per run (no cache file — staying T2). Existing
`scan()` output shape (`list[Project]` sorted by `usage.total` desc, merge-by-cwd) is
**unchanged** except `Project` gains `by_model` and a `cost` property; `Usage` and `Session`
keep their existing fields (`Session` gains `by_model`). Existing `_write_session`
lines without `timestamp`/`model` still parse (they bucket under family `default`, day
`unknown`), so AC-DATA-1..5 keep passing. The new daily ACs need those fields, so
`_write_session` is **extended backward-compatibly**: each `rows` dict may carry optional
`"timestamp"` and `"model"` keys that the helper lifts to the line top-level / `message.model`
respectively, e.g. `{"input_tokens": 10, "timestamp": "2026-06-16T02:30:00Z", "model":
"claude-opus-4-8"}` → line `{"timestamp": "...", "message": {"model": "...", "usage":
{"input_tokens": 10}}}`. Rows omitting them behave exactly as today.

## Behavior

- **Launch / scope.** The console entry point is `cctop = "cctop.app:main"` (verified
  `pyproject.toml`) and `python -m cctop` delegates to the same `cctop.app:main` — so
  `parse_scope` and the argv read live in **`app.py`**, not `__main__.py` (which stays a thin
  re-export). `main()` calls `parse_scope(sys.argv[1:])`, which returns `True` iff `--global`
  or `-g` is present, then constructs `CCTop(global_scope=...)`.
  `CCTop.scope_cwd = None if global_scope else os.getcwd()`, captured **once at construction**
  (not re-read on rescans). Textual no-ops unhandled key bindings, so leaderboard keys
  (`c`/`m`/…) simply do nothing while the daily screen is active — no error.
- **Scan.** The threaded `@work` scan calls `scan_daily(cwd=self.scope_cwd)` for the daily
  view and `scan(merge_by_cwd=...)` for the leaderboard (filtered to `scope_cwd` at render).
  One scan pass populates both; `g` (`action_toggle_scope`) flips `scope_cwd` and reloads.
- **Day bucketing.** Per usage line, `day = _local_day(timestamp)`. `_local_day` normalizes a
  trailing `Z` to `+00:00` before `datetime.fromisoformat` (Python ≥3.9 floor — bare `Z` is
  rejected before 3.11), parses, `.astimezone()` to local, formats `%Y-%m-%d`. Missing or
  unparseable timestamp → `"unknown"`; that line is still counted (never dropped). `"unknown"`
  sorts after all real days.
- **Family & cost.** `family_of(model)` lowercases and substring-matches in priority order
  opus→sonnet→haiku→fable; `"<synthetic>"` → `"synthetic"`; anything else (incl. `None`) →
  `"default"`. `cost_of(usage, family)` applies `RATES[family]` per token class / 1e6.
- **Totals.** A `DayUsage.cost` sums every family present (so a `default`/`other` family with
  no column still contributes to `EST $`); `<synthetic>` contributes $0. `CLIENT $` =
  `cost × MARGIN`.
- **Edge cases:** a day with only synthetic activity shows all-zero columns and `EST $ $0.00`;
  a directory with no matching transcripts → empty daily table + empty-state summary;
  `CCTOP_MARGIN` unset → `CLIENT $` equals `EST $`.

## Acceptance Criteria

- **AC-DATA-6**: WHEN `family_of` is given a model id THE SYSTEM SHALL map by case-insensitive
  substring (`"claude-opus-4-8"`→`"opus"`, `"claude-sonnet-4-6"`→`"sonnet"`,
  `"claude-fable-5"`→`"fable"`, `"claude-haiku-4-5"`→`"haiku"`, `"<synthetic>"`→`"synthetic"`,
  `"mystery-model"`→`"default"`, `None`→`"default"`) → `test_family_of_maps_by_substring` in
  `tests/test_data.py`.
- **AC-DATA-7**: WHEN `cost_of` prices a usage THE SYSTEM SHALL use that family's rates
  (`cost_of(Usage(input=1_000_000), "opus")` → `RATES["opus"].input`; `cost_of(Usage(input=1_000_000,
  output=1_000_000), "synthetic")` → `0.0`) → `test_cost_of_uses_family_rates` in
  `tests/test_data.py`.
- **AC-DATA-8**: WHEN `CCTOP_MARGIN=1.5` THE SYSTEM SHALL compute `client_cost(10.0)` → `15.0`,
  and WHEN unset SHALL compute `client_cost(10.0)` → `10.0` (monkeypatch `data.MARGIN`) →
  `test_client_cost_applies_margin` in `tests/test_data.py`.
- **AC-DATA-9**: WHEN two sessions in the same cwd have usage on the same local day with
  different models THE SYSTEM SHALL return one `DayUsage` whose `by_model` has a per-family
  `Usage` and whose `cost` == sum of per-family `cost_of` (with `monkeypatch.setenv("TZ","UTC")`
  **then `time.tzset()`**, a line at `2026-06-16T02:30:00Z` → day `"2026-06-16"`) →
  `test_scan_daily_buckets_by_day_and_model` in `tests/test_data.py`.
- **AC-DATA-10**: WHEN `scan_daily(cwd="/work/a")` runs over sessions in `/work/a` and `/work/b`
  THE SYSTEM SHALL include only `/work/a` days, and WHEN `cwd=None` SHALL include both →
  `test_scan_daily_filters_by_cwd` in `tests/test_data.py`.
- **AC-DATA-11**: WHEN a usage line has a missing or unparseable `timestamp` THE SYSTEM SHALL
  bucket it under day `"unknown"` (which sorts **after** every real day in the returned list)
  and still count its tokens, and WHEN the timestamp ends in `Z` SHALL parse it without raising
  (assert e.g. `_local_day("2026-06-16T02:30:00Z")` does not raise and is not `"unknown"`) →
  `test_scan_daily_handles_bad_timestamp` in `tests/test_data.py`.
- **AC-DATA-12**: WHEN a project mixes models THE SYSTEM SHALL compute `Project.cost` as the sum
  of per-family costs (1M opus output + 1M sonnet output → `RATES["opus"].output +
  RATES["sonnet"].output`), not a blended rate on summed tokens →
  `test_project_cost_sums_per_model` in `tests/test_data.py`.
- **AC-APP-2**: WHEN the app mounts with no flag THE SYSTEM SHALL start in `"daily"` mode with a
  cwd scope and a daily table present → `test_app_starts_in_daily_mode` in `tests/test_app.py`.
- **AC-APP-3**: WHEN `parse_scope` (imported as `from cctop.app import parse_scope`) is given
  argv THE SYSTEM SHALL return `True` for `["--global"]` and `["-g"]` and `False` for `[]`; and
  `CCTop(global_scope=True).scope_cwd is None` while `CCTop(global_scope=False).scope_cwd ==
  os.getcwd()` → `test_parse_scope_and_app_scope` in `tests/test_app.py`.
- **AC-APP-4**: WHEN `d`, `p`, and `g` are pressed THE SYSTEM SHALL switch to daily mode,
  projects mode, and toggle scope respectively without error (and the leaderboard's
  `c`/`m`/`t`/`/` still dispatch in projects mode) → `test_app_mode_and_scope_bindings` in
  `tests/test_app.py`.
- **AC-APP-1** (existing, **updated**): the test must press `p` to enter projects mode before
  exercising the leaderboard keys (`c`/`m`/`t`/`slash`/`escape`), since those bindings now live
  on `ProjectsScreen` and the app starts in daily mode; `app.projects` is still populated by
  `CCTop`'s threaded scan before the poll. **AC-DATA-1..5** (existing) SHALL continue to pass
  **unchanged** (backward compatibility of `Usage.cost`, `RATE_INPUT/OUTPUT`, `scan` merge,
  `shorten`).

## Assumptions (escalation triggers)

- **A1**: No public list price for `claude-fable-5` is known at plan time, so `fable` is seeded
  at opus-tier rates as a visible placeholder. **If false / when known:** set the real numbers
  in the `RATES["fable"]` seed (still env-overridable via `CCTOP_RATE_FABLE_*`); no structural
  change.
- **A2**: Every usage-bearing JSONL line carries a co-located top-level `timestamp` and
  `message.model` (verified: 22,318/22,318 sampled lines have both). **If false:** the missing
  field degrades gracefully — no timestamp → day `"unknown"`; no model → family `"default"`;
  never raises.
- **A3**: `os.getcwd()` at launch equals the transcript `cwd` string for the current project
  (Claude Code records the absolute working dir). **If false:** the daily view shows the
  empty-state hint and `g` reaches the data; consider documenting that cctop must be launched
  from the project root. STOP and ask the user only if the cwd encoding proves systematically
  mismatched.
- **A4**: Public seed rates (opus 15/75, sonnet 3/15, haiku 1/5; cache-write = 1.25× input,
  cache-read = 0.1× input) are acceptable approximations; users with negotiated rates override
  via env. **If false:** adjust the seed literals in `RATES` — they are the single source.
- **A5**: Reworking `app.py` into `MODES` does not regress the existing leaderboard/drill
  behavior. **If false (a binding or the drill breaks):** the app-boot check
  (`uv run pytest tests/test_app.py`) catches it; keep `ProjectsScreen` a verbatim move of the
  current logic.

## Rationale

The request reframes cctop from a directory leaderboard into a **time × model cost report** for
the current project, with a billing markup. D1/D2 follow the user's explicit choices (daily
default, day-rows × model-columns, `$cost(tok)` cells). The load-bearing engineering call is
D3: once each cell shows a per-model dollar figure, the existing single blended rate would be
actively misleading (opus is ~5× sonnet), so a per-family rate table is required, not optional.
It is structured to preserve every existing invariant — `RATE_*` constants and `Usage.cost`
stay as the `default` family, so `AC-DATA-2` and all current behavior survive — while routing
the leaderboard and daily views through accurate per-family sums (`Project.cost`, `DayUsage.cost`).

D5 (local day) matches a consultant's wall-clock intuition; the risk is test determinism, bought
back by `TZ=UTC` + `tzset()` in tests and an explicit literal example in AC-DATA-9. D6 uses
Textual's `MODES` rather than modal stacking because daily and the leaderboard are peer views the
user should freely switch between; the cost is an `app.py` refactor (leaderboard → `ProjectsScreen`),
de-risked by keeping that move verbatim and leaning on the headless app-boot test.

The sharpest fragility is timestamp parsing: the project floor is `>=3.9`, but `datetime.fromisoformat`
rejects a trailing `Z` before 3.11, and **all** real timestamps end in `Z` — hence the explicit
`Z`→`+00:00` normalization in `_local_day`, called out so a worker on a 3.13 dev box doesn't ship a
3.9-broken parser. Fable pricing (A1) is an honest unknown seeded as a placeholder rather than
guessed precisely.

One refuter finding was **rejected**: that `docs/canonical/daily-cost-view.md` should be a
File Plan `CREATE` row. Per the pipeline's Canonical Docs Loop (shared invariants), the
Canonical Delta is applied by `/spec:review` on `done`, not authored by `/spec:build` workers —
so it correctly stays out of the File Plan. All other refuter findings were fixed in-spec:
entry point moved to `app.py:main`, `action_refresh`/`#summary` re-scoped to the active screen,
AC-APP-1 updated to enter projects mode, the `"unknown"`-day sort corrected to a two-list form,
the SessionsScreen verbatim-vs-accurate contradiction reconciled via `Session.cost`,
`field(default_factory=dict)` pinned, `_write_session` extension specified, and `os.tzset()`
made explicit.

## Canonical Delta

Create `docs/canonical/daily-cost-view.md` capturing, for future T1 work: the model-family rate
table pattern (`RATES` keyed by family, `CCTOP_RATE_<FAMILY>_<CLASS>` overrides, `default`
fallback, `synthetic`=$0), the `family_of` substring-resolution rule, the local-day bucketing
contract (`Z`-normalized, `"unknown"` sentinel sorts last), the `CCTOP_MARGIN` markup-multiplier
semantics, and the two-view `MODES` structure (`daily` default + `projects` leaderboard, `g`
scope toggle). Note that adding a new model family is now T1 (mirror a `RATES` row + the
`FAMILIES` column tuple), and that cost is per-model accurate everywhere via `cost_of`.
