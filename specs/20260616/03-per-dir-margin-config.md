---
date: 2026-06-16
status: implementing
risk: T3
area: data
design: false
breaking: false
depends_on: ["02-cwd-only-daily"]
depended_on_by: ["04-row-select-csv-copy"]
---

# Per-directory margin config (`.cctop`)

## Goal

Replace the global `CCTOP_MARGIN` env-only markup with a per-directory client margin that
lives in a `.cctop` file in the directory cctop is launched from, and is editable from inside
the TUI. On launch cctop reads the launch dir's `.cctop` margin; the user can edit the margin
with a key, and on submit cctop **writes** `.cctop` so the directory remembers its markup.
"Done" means each client directory carries its own margin in `.cctop`, editing persists it, and
a missing/garbled `.cctop` silently falls back to the env/default. **This spec is T3**: it is
the first time cctop writes to disk (a user directory) — the mid-build T3 triggers in pipeline
rules § Risk Tiers ("writes anything under a user directory", "introduces a persisted state
file") both fire, so the write surface gets the T3 checkpoint before it lands.

## Decisions (locked — workers apply verbatim, never override)

| ID | Decision | One-line rationale |
|----|----------|--------------------|
| D1 | Config file is `<launch cwd>/.cctop`, JSON, schema `{"margin": <number>}`. Filename constant `DIR_CONFIG_NAME = ".cctop"` in `data.py`. Extra keys are ignored (forward-compatible); only `margin` is read. | User asked for a `.cctop` file per dir; JSON is stdlib, no new dependency. |
| D2 | cctop writes `.cctop` **only on an in-app margin edit**, never on launch. No file is created just by viewing a directory. | User choice: "write on first margin edit"; no surprise files in untouched repos. |
| D3 | Launch margin precedence: a **valid** `<cwd>/.cctop` margin → else the env value `data.MARGIN` (already initialized from `CCTOP_MARGIN` at import, data.py:74) → else `1.0`. Literal: `.cctop` `{"margin": 2.0}` with `CCTOP_MARGIN=1.3` set → active margin `2.0`. | `.cctop` is the per-dir source of truth; env stays as a global default seed. |
| D4 | The active margin **is** `data.MARGIN` (the existing module global that `client_cost` reads, data.py:105–107). `set_margin(value)` mutates it via `globals()["MARGIN"] = value` **inside data.py** (NOT a `from cctop.data import MARGIN` rebind — that would not update the module attribute). `current_margin()` returns `MARGIN`. `client_cost` is **unchanged**. | Single cost chokepoint in `data.py` (number discipline) **and** keeps `tests/test_data.py:140` green (it `monkeypatch.setattr(data,"MARGIN",…)` — the same mutation path). |
| D5 | `read_dir_margin` and `write_dir_margin` are **best-effort** like `_parse_file`: read swallows `OSError` / `json.JSONDecodeError` and returns `None` for any value that is not a finite, non-negative, non-bool real number; write swallows `OSError`, cleans up its temp file, and returns `False`. Neither raises; neither `print()`s. | Preserve robustness; a read-only dir, a hand-corrupted `.cctop`, or a negative/`inf` value must never break or poison the TUI. |
| D6 | Margin only. `.cctop` does **not** carry rate overrides; model `$/MTok` rates stay in `data.py`'s `RATES`. | User choice; smallest schema; rates are list prices, identical everywhere. |
| D7 | Write is atomic: `tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=".cctop.", suffix=".tmp", delete=False)` in the **same directory**, then `os.replace(tmp, <dir>/.cctop)`. On any `OSError`, unlink the temp file (best-effort) and return `False`. | Same-dir temp + `os.replace` is atomic on one filesystem (macOS/POSIX); a crash mid-write never corrupts or orphans the file. |
| D8 | Edit binding is `e` ("edit margin") on `DailyScreen`; `escape` cancels (`action_cancel_margin`). `e` reveals an inline `Input` (`id="margin-input"`, the docked-Input pattern the removed projects filter used) started **empty** with `placeholder=str(current_margin())` (the current margin shown only as a placeholder hint — **not** a prefilled value). On Enter, `float(input.value)` is parsed; if finite and `>= 0` (D5) → `set_margin`; an empty or invalid/negative/`inf`/`nan` value → no change, no write, hide input. | `e`/`escape` are free after spec `02`; reuse the proven Input pattern (the pre-02 projects filter started **empty** with a `placeholder=`) — no new modal (host cap: ≤1 modal/spec; we add none). Cancel mirrors the old `action_clear_filter`. **Amended during build (retainer ruling 2026-06-16):** original "prefilled with the current margin" contradicted AC-CFG-6 (typing `2.5` into a prefilled `"1.0"` yields `"1.02.5"` → `ValueError`); placeholder preserves the see-current-margin intent with no AC change. |

## File Plan

| Path | Action | Layer | Summary |
|------|--------|-------|---------|
| src/cctop/data.py | MODIFY | data | Add `import math`, `import tempfile`. Add `DIR_CONFIG_NAME`, `read_dir_margin(directory) -> float \| None` (validity per D5), `write_dir_margin(directory, margin) -> bool` (atomic + temp cleanup per D7), `set_margin(value)` (`globals()["MARGIN"] = value`), `current_margin() -> float`. `client_cost` unchanged (still reads `MARGIN`). |
| src/cctop/app.py | MODIFY | tui | Re-add the `Input` import (spec `02` removed it as unused). Add `CCTop._margin_source: str` attribute (`".cctop"`/`"env"`/`"unset"`). On launch (`on_mount`/`__init__`): `env_margin = data.current_margin()`; `m = read_dir_margin(self._launch_cwd)`; `set_margin(m if m is not None else env_margin)`; set `_margin_source`. Rewrite `_margin_label` to use `current_margin()` + `_margin_source` (drop the `m == 1.0` value test). `DailyScreen`: `BINDINGS = [Binding("e","edit_margin",…), Binding("escape","cancel_margin",…,show=False)]`; inline `#margin-input` Input + CSS; `action_edit_margin` (reveal, prefill, focus); `action_cancel_margin` (hide, refocus); `on_input_submitted` (validate → `set_margin` → `write_dir_margin(launch_cwd, v)` → set source `.cctop` → re-render via `refresh_daily`; on write failure show note). |
| README.md | MODIFY | other | Document `.cctop`: file location + JSON shape, the `e` edit key + `escape` cancel, precedence (`.cctop` → `CCTOP_MARGIN` → 1.0), write-on-edit, and a suggested `.gitignore` line (`.cctop`). Update the "Client margin" section. |
| tests/test_data.py | MODIFY | tests | AC-CFG-1..4 — read present/absent/malformed/negative/inf/string/bool; atomic write round-trip; write to read-only dir → `False`, no raise, no orphan temp; `set_margin`/`current_margin`/`client_cost` integration. Restore `data.MARGIN` via `monkeypatch`. |
| tests/test_app.py | MODIFY | tests | AC-CFG-5, AC-CFG-6 — launch reads `.cctop` into the active margin (construct `CCTop(launch_cwd=str(tmp_path))`); `e` + submit sets and writes; invalid submit leaves margin unchanged and writes nothing. `monkeypatch.setattr(data,"MARGIN", <orig>)` so the module-global mutation does not leak across tests. |

## Contracts

```python
# src/cctop/data.py  (add `import math`, `import tempfile` at top)
DIR_CONFIG_NAME = ".cctop"

def read_dir_margin(directory: str | Path) -> float | None:
    """Margin from <directory>/.cctop, or None if absent/unreadable/malformed/out-of-range.
    Best-effort — never raises. Returns None unless the JSON is an object whose "margin"
    is a real number (NOT bool, NOT a numeric string) that is finite and >= 0:
        try:  obj = json.load(open(<dir>/.cctop))
        except (OSError, json.JSONDecodeError): return None
        val = obj.get("margin") if isinstance(obj, dict) else None
        if isinstance(val, bool) or not isinstance(val, (int, float)): return None
        f = float(val)
        return f if (math.isfinite(f) and f >= 0) else None
    """

def write_dir_margin(directory: str | Path, margin: float) -> bool:
    """Write {"margin": margin} to <directory>/.cctop atomically. Return True on success,
    False on OSError. Never raises, never prints.
        tmp = None
        try:
            f = tempfile.NamedTemporaryFile("w", dir=directory, prefix=".cctop.",
                                            suffix=".tmp", delete=False); tmp = f.name
            with f: json.dump({"margin": margin}, f)
            os.replace(tmp, Path(directory) / DIR_CONFIG_NAME); return True
        except OSError:
            if tmp: 
                try: os.unlink(tmp)
                except OSError: pass
            return False
    """

def set_margin(value: float) -> None:
    """Set the active client margin. MUST mutate the module global so client_cost sees it:
        globals()["MARGIN"] = value
    (A `from cctop.data import MARGIN` rebind would NOT update the module attribute.)"""

def current_margin() -> float:
    """The active client margin (the live module-level MARGIN)."""

# client_cost is UNCHANGED — still `return cost * MARGIN`.
```

```python
# src/cctop/app.py — CCTop instance attribute (set at launch, updated on edit)
self._margin_source: str  # one of ".cctop", "env", "unset"
```

`.cctop` on-disk shape (D1): one JSON object, e.g. `{"margin": 2.0}`.

## Data Model

New persisted file `<launch cwd>/.cctop` (JSON), **created only by an in-app margin edit**
(D2). No prior persisted state exists to migrate (every run was a fresh in-memory scan).
Reading is best-effort; a malformed/out-of-range file is treated as absent and overwritten on
the next successful edit (atomic, D7).

## UI

- **Margin edit Input** (`DailyScreen`): an `Input`, `id="margin-input"`, docked bottom, hidden
  (`display: none`) until `e`, then `.visible` and focused, started **empty** with
  `placeholder=str(current_margin())` (D8 — the current margin is a placeholder hint, not a
  prefilled value). Mirrors the docked-Input CSS/visibility pattern the projects filter used (the
  `#filter` Input in `ProjectsScreen` before spec `02`, which also started empty with a placeholder).
- **Component API (embedded — workers do not query MCP).** Textual pinned **8.2.7** (verified
  against `.venv`): `textual.widgets.Input` — `.value: str`, `.focus()`,
  `.add_class("visible")` / `.remove_class("visible")`; emits `Input.Submitted` (`event.value`)
  on Enter, handled by `def on_input_submitted(self, event: Input.Submitted) -> None`. No new
  widget type, no modal — the existing in-repo pattern.
- **Margin label** (summary, replacing the value-based `_margin_label`): `2.0 (.cctop)` /
  `1.3 (env)` / `1.0 (unset)`, driven by `_margin_source`, not by `current_margin() == 1.0`.
  After a failed write, append `(could not write .cctop)`.

## Behavior

- **Launch:** capture `env_margin = current_margin()` (the import-time env value) **before** any
  `set_margin`; then `m = read_dir_margin(self._launch_cwd)`; `set_margin(m if m is not None else
  env_margin)`. Set `_margin_source`: `".cctop"` if `m is not None`; else `"env"` if
  `env_margin != 1.0`; else `"unset"`.
- **Edit:** `e` reveals `#margin-input` started **empty** with `placeholder=str(current_margin())`
  (D8 — placeholder hint, not a prefilled value) and focuses it. `escape` → `action_cancel_margin`
  hides it and refocuses the table (no change). On `Input.Submitted`: parse
  `float(event.value.strip())`; an empty value (`ValueError`) is a no-op.
  - Valid `v` with `math.isfinite(v) and v >= 0`: `set_margin(v)`; `ok = write_dir_margin(
    self._launch_cwd, v)`; `_margin_source = ".cctop"`; hide input; `refresh_daily()` (re-render
    only — do **not** re-run `load_data`/`scan_daily`; `DayUsage.client` recomputes lazily from
    `MARGIN`, data.py:241–243). If `not ok`, surface `(could not write .cctop)` in the label.
  - Invalid (`ValueError`, `inf`, `nan`, or `< 0`): hide input, margin unchanged, **no write**.
- **No stdout:** every failure path surfaces via the summary widget; never `print()`, never
  raise into the event loop.

## Acceptance Criteria

<!-- T3 — every AC carries a literal example. -->

- **AC-CFG-1**: WHEN `<D>/.cctop` is `{"margin": 2.0}` THE SYSTEM SHALL return
  `read_dir_margin(D) == 2.0`. → `test_data.py::test_read_dir_margin_present`
- **AC-CFG-2**: WHEN `<D>` has no `.cctop`, or it is unparseable, or `margin` is missing /
  non-numeric / a string / bool / negative / non-finite, THE SYSTEM SHALL return `None` without
  raising. Literals: no file → `None`; `not json{` → `None`; `{"foo": 1}` → `None`;
  `{"margin": "2.0"}` → `None`; `{"margin": true}` → `None`; `{"margin": -1.0}` → `None`;
  `{"margin": Infinity}` → `None`. → `test_data.py::test_read_dir_margin_absent_or_malformed`
- **AC-CFG-3**: WHEN `write_dir_margin(D, 1.5)` runs on a writable `D` THE SYSTEM SHALL create
  `<D>/.cctop` parsing to `{"margin": 1.5}`, leave no `.cctop.*​.tmp` file behind,
  `read_dir_margin(D) == 1.5`, and return `True`. → `test_data.py::test_write_dir_margin_roundtrip`
- **AC-CFG-4**: WHEN `write_dir_margin(D, 2.0)` runs and `D` is not writable THE SYSTEM SHALL
  return `False`, not raise, leave any existing `.cctop` intact, and leave no temp file. →
  `test_data.py::test_write_dir_margin_readonly_returns_false`
- **AC-CFG-5**: WHEN the app launches via `CCTop(launch_cwd=D)` where `D/.cctop` is
  `{"margin": 3.0}` THE SYSTEM SHALL set the active margin to `3.0`, so `client_cost(10.0) ==
  30.0` — even with `CCTOP_MARGIN=1.3` set (precedence: `.cctop` over env). →
  `test_app.py::test_launch_reads_dotcctop_margin`
- **AC-CFG-6**: WHEN the user presses `e`, types `2.5`, and submits THE SYSTEM SHALL set the
  active margin to `2.5` (`client_cost(10.0) == 25.0`) AND call `write_dir_margin(launch_cwd,
  2.5)`; WHEN the submitted value is `abc`, `-1`, or `inf`, the margin SHALL be unchanged and no
  write SHALL occur. → `test_app.py::test_edit_margin_sets_and_writes`,
  `test_app.py::test_edit_margin_invalid_no_change`

## Assumptions (escalation triggers)

- A1: Writing a single `.cctop` dotfile into the launch cwd is the approved T3 write surface.
  **If false:** STOP — the write is the whole point; do not silently fall back to env-only.
- A2: `client_cost` (data.py:105) is the **only** place margin is applied (everything flows
  through it: `DayUsage.client` data.py:241–243, the summary `client_cost(agg_cost)` app.py:254).
  Keeping `client_cost` reading module `MARGIN` and reassigning `MARGIN` via `set_margin` updates
  every client figure. **If false (a second multiply exists):** route it through
  `current_margin()` too, in this batch.
- A3: `tests/test_data.py:140` (`test_client_cost_applies_margin`) `monkeypatch.setattr(data,
  "MARGIN", …)` and must stay green. D4 preserves this. **If false (it breaks):** the design
  regressed — fix the implementation, do not weaken the test.
- A4: The launch cwd (`self._launch_cwd`, app.py:440 / the new `launch_cwd` param from spec `02`)
  is the directory to bill and write `.cctop` into. **If false:** still write to the launch cwd —
  never under `~/.claude`.
- A5: `os.replace` within the same directory is atomic on macOS/POSIX (this repo's platform).
  **If false:** fall back to a direct `open(...,"w")` guarded by the same `OSError` swallow.
- A6: TUI tests mutate `data.MARGIN` through `set_margin` (a raw module-global assignment that
  pytest does NOT auto-restore). They must snapshot/restore it (`monkeypatch.setattr(data,
  "MARGIN", <orig>)`) to avoid leaking into later tests. **If false (tests share state cleanly):**
  still restore — module-global leakage is the likeliest flake source here.

## Rationale

The active-margin-is-`data.MARGIN` choice (D4) is load-bearing: it keeps cost math in one
`data.py` chokepoint (number discipline), avoids threading a margin argument through
`client_cost` and callers, and keeps `test_client_cost_applies_margin` green because it
monkeypatches `data.MARGIN` — the live value `client_cost` reads. The refuters flagged the trap:
`set_margin` MUST mutate the module attribute (`globals()["MARGIN"] = value`), not rebind a
`from`-imported name, or `client_cost`/`_margin()` would read stale values — D4/Contracts now pin
this. Validity guards (D5): a hand-edited `.cctop` with `-1.0`, `Infinity` (non-standard JSON
that `json` round-trips), `true`, or `"2.0"` must all degrade to the fallback rather than poison
every CLIENT $ — `float('inf') >= 0` is `True`, so `math.isfinite` is required, not optional. The
same finite-and-non-negative gate guards the edit submit. Write-on-edit (D2) + atomic temp-file
write with cleanup (D7) avoids dropping files into untouched repos and never orphans a
`.cctop.*.tmp`. The Input pattern (D8) reuses the projects filter mechanics (removed in `02`,
`Input` re-imported here), so no new Textual surface and no modal. **Rejected finding** (refuter,
spec-03-B #9): adding `docs/canonical/daily-cost-view.md` to the File Plan — per shared invariants
§ Canonical Docs Loop the Canonical Delta is applied by `/spec:review` on `done`, not built as a
batch row; the README (user docs) is the File Plan row. Fragile spot: the write path is the
tool's first disk write — the T3 checkpoint covers it, and AC-CFG-4 is the guard that a read-only
dir degrades instead of crashing.

## Canonical Delta

`docs/canonical/daily-cost-view.md`: add a **Per-directory margin** subsection. The client
markup now lives in `<launch dir>/.cctop` as JSON `{"margin": <number>}`. cctop reads it on
launch (precedence: `.cctop` → `CCTOP_MARGIN` env → `1.0`) and writes it when the user edits the
margin in-app with `e` (`escape` cancels). The file is created only on edit, read best-effort (a
missing, malformed, negative, or non-finite value falls back silently), and written atomically.
Model `$/MTok` rates remain global in `data.py`; only the margin is per-directory. Suggest adding
`.cctop` to `.gitignore`.
