# Spec Pipeline Rules — cctab

Repo-specific grounding for the spec pipeline. `cctab` is a single-process, read-only
[Textual](https://textual.textualize.io/) TUI that scans Claude Code's local JSONL
transcripts (`~/.claude/projects/<encoded-cwd>/*.jsonl`) and rolls up token usage per
directory. Two source modules: `src/cctab/data.py` (the foundation: parsing, aggregation,
cost math) and `src/cctab/app.py` (the TUI). `§ Worker Rules` and `§ Test Rules` are inlined
verbatim into worker prompts by `/spec:build`.

## Risk Tiers

cctab has **no standing T3 surfaces**. It is a read-only viewer: no auth, no permission logic,
no persisted writes, no migrations, no money *mutation* (the `EST $` column is a display-only
estimate), and no cross-process or cross-area contracts. Treat the ceiling as **T2-max** unless
a spec introduces one of the mid-build upgrade triggers below.

**Mid-build T3 upgrade triggers** — if a spec starts to do any of these, upgrade to T3 from
that point, note it in the spec, and apply the `§ Build` checkpoint:

- Writes, deletes, or moves anything under `~/.claude` or any user directory (today the tool
  only *reads* there).
- Shells out / executes an external process, or adds a network call.
- Introduces a persisted cache or state file (today every run is a fresh in-memory scan).

**T1 (no spec, direct work, gated by `gateCommand`):** single module; an established
in-repo pattern — a new sort key mirroring `SORTABLE`, a new column mirroring `COLUMNS`, a new
key binding mirroring an existing `Binding`/`action_*` pair, or a formatting tweak inside
`human`/`num_cell`/`cost_cell`; no new assumption about the transcript schema.

**T2:** everything else — new assumptions about the JSONL schema, new aggregation/merge
semantics, a new modal screen, or changes spanning both `data.py` and `app.py`. Enters the
pipeline only when it needs delegation or durability (see shared § Pipeline Entry).

## Planning

- **Discovery surfaces.** There are no generated contract files in this repo. The one external
  contract is **Claude Code's transcript schema**, encoded in `src/cctab/data.py:_parse_file`
  (`obj["cwd"]`, `message.usage.{input_tokens, output_tokens, cache_creation_input_tokens,
  cache_read_input_tokens}`). Ground new data-shape work against **real logs**: inspect an
  actual transcript (`ls ~/.claude/projects/*/ | head`, then read a `*.jsonl`) rather than
  guessing keys. Read the existing `docs/canonical/*.md` if present.
- **Pre-emptive MCP lookups (plan time only).** Textual is the sole third-party dependency.
  When a UI section introduces a **new Textual surface** (a new widget, `Screen`/`ModalScreen`,
  reactive, `@work`, or message handler), resolve the API via Context7 (`resolve-library-id`
  → `textual`, then `query-docs`) and **embed the resolved shape into the spec's UI/Contracts
  section** so build workers never query MCP. There is no UI component registry (TUI).
- **Decomposition caps** (beyond the generic ≤15 File Plan rows): at most **one new modal
  screen** per spec. Keep `data.py` and `app.py` changes in one spec only when the TUI change
  consumes the new data shape; otherwise split by landing unit (each spec leaves the gate green).
- **New-surface checklist:**
  - *Requirements interview:* which columns / sort keys / filters / key bindings; what a row
    and the drill-down show.
  - *Data-shape design:* what fields `Usage`/`Session`/`Project` need; how cost/rates are
    handled (rates stay in `data.py`).
  - *Env surface:* any new `CCTAB_*` or `CLAUDE_PROJECTS_DIR` semantics, documented in both
    `README.md` and the `data.py` rate block.
  - *Wiring rows the structure demands:* a new key binding → File Plan rows for `CCTab.BINDINGS`
    **and** an `action_*` method **and** the README key table; a new column → `COLUMNS` **and**
    `SORTABLE` **and** the `refresh_table` row build (**and** a `Binding` if it's sortable).

## Build

cctab has no codegen, migrations, i18n, or route generation — orchestrator integration duties
are light:

- After a green phase that touches `src/cctab/app.py`, run the **app-boot check**:
  `uv run pytest tests/test_app.py` — it mounts the full Textual app headlessly and dispatches
  the key bindings.
- After data-layer changes, an optional **real-log smoke**:
  `uv run python -c "from cctab.data import scan; print(len(scan()))"` (the unit tests already
  cover aggregation with synthetic dirs).
- If a spec adds a third-party dependency, the **orchestrator** (never a worker) runs
  `uv sync` and commits the updated `uv.lock`.

**Host escalation triggers (consult the Fable retainer):** a change needs to write under
`~/.claude`/a user dir; a change needs a new dependency; the `_parse_file` schema assumption
proves wrong against a real transcript (a stale embedded reference). Each may also signal a
**T3 upgrade**.

**T3 checkpoint surfaces:** none standing. If a spec is upgraded to T3 mid-build, checkpoint
Fable before landing the write / exec / network surface that triggered the upgrade.

## Worker Rules

- **Managed / read-only surfaces.** `uv.lock` is owned by uv — never hand-edit it; dependency
  changes go through `pyproject.toml` plus the orchestrator running `uv sync`. Never touch
  `.venv/` or `__pycache__/`.
- **Layer boundary.** `src/cctab/data.py` is the foundation layer and MUST NOT import from
  `cctab.app` (no `from cctab.app …`, no `import cctab.app`). The dependency is one-way:
  `app.py` imports from `data.py`. A reverse import is a hard finding.
- **Number / cost discipline.** The public `$/MTok` rates and their env overrides live ONLY in
  `src/cctab/data.py` as the `RATE_*` module constants; cost is computed by `Usage.cost`. Never
  hardcode a rate or a token-class weight elsewhere. Number/`$` *formatting* lives in `app.py`
  helpers `human` / `num_cell` / `cost_cell` — reuse them; don't re-derive formatting inline.
- **Robustness.** Parsing is best-effort by design: `_parse_file` swallows `OSError` and
  per-line `json.JSONDecodeError` so one bad transcript never breaks a scan. Preserve this — a
  new parse path must not raise on malformed input or missing keys.
- **No stdout in the TUI.** Never `print()` from `src/cctab/app.py` or `src/cctab/data.py` — it
  corrupts the Textual screen. User-facing text goes through widgets. `print()` is allowed only
  in standalone scripts (`smoke_test.py`) and tests.
- **Scoped self-verify.** Workers may run `uv run ruff check <their files>` and
  `uv run pytest <their test files>`. Nothing else.

## Test Rules

- **Placement & naming.** pytest tests live in `tests/`, files named `tests/test_*.py`,
  functions `test_<behavior>` (one behavior per test). The repo-root `smoke_test.py` is a
  legacy standalone demo — do not add tests there; mirror its headless pattern in
  `tests/test_app.py`.
- **AC-ID reference.** In the test function's **docstring, first line**, e.g.
  `"""AC-DATA-3: two encoded folders with the same cwd merge into one row."""`. Convention set
  by `tests/test_data.py`.
- **Fixtures.** Data-layer tests build synthetic transcript dirs under `tmp_path` and pass
  `projects_dir=` to `scan(...)` — never read the real `~/.claude`. Use `monkeypatch` for
  module constants (`HOME`, `RATE_*`). The `_write_session` helper in `tests/test_data.py` is
  the canonical fixture builder.
- **TUI tests.** Drive the app via `App.run_test()` wrapped in `asyncio.run(...)` — no async
  plugin is installed. See `tests/test_app.py`.
- **TDD exemption.** Pure-render details (exact cell styling, the `heat`/cost color
  thresholds) are NOT unit-tested — they're verified by eye in the running app. TDD applies to
  data-layer logic (parsing, aggregation, cost, merge) and to binding/action dispatch (a key
  fires the right `action_*` and mutates state), not to how a cell looks.

## Review Checks

Severity calibrations for the reviewer (each phrasing is file:line-verifiable):

- `from cctab.app` / `import cctab.app` anywhere in `src/cctab/data.py` → **hard**
  (layer-boundary violation; data is the foundation).
- A `$/MTok` rate literal or token-class weight outside `data.py`'s `RATE_*` constants /
  `Usage.cost` → **hard** (number discipline).
- `print(` in `src/cctab/app.py` or `src/cctab/data.py` → **hard** (corrupts the TUI; scripts
  and tests are exempt).
- A `uv.lock` diff with no corresponding `pyproject.toml` dependency change → **hard**
  (hand-edited managed surface).
- A new key binding without its `action_*` method, or a new `COLUMNS` entry without the
  matching `SORTABLE` + `refresh_table` row wiring → **hard** (half-wired surface).
- A `_parse_file` change that can raise on malformed JSON, missing keys, or an unreadable file
  (drops the best-effort guards) → **hard**.
- A new `CCTAB_*` / `CLAUDE_PROJECTS_DIR` env surface not documented in `README.md` and the
  `data.py` rate block → **soft**.
- Bare `except:` instead of the specific `except OSError` / `except json.JSONDecodeError` the
  code already uses → **soft**.
- A third-party dependency added inside a worker batch rather than via the orchestrator's
  `uv sync` → **soft** (process, not correctness).
