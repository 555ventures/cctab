# cctab

**Turn your Claude Code usage into an API-rate cost estimate you can bill a client — daily,
per-model, per directory, right in your terminal.**

`cctab` reads the transcripts Claude Code already writes to disk, re-prices your actual token
usage at public API list rates, applies your client markup, and lets you copy a clean,
spreadsheet-ready CSV for invoicing — without leaving the terminal and without sending anything
anywhere.

```
┌ cctab ─────────────────────────── Claude Code usage → billable cost ┐
│ cwd: ~/Projects/wbm-booking · 6 days   4.2M tok   $58.41 est   $87.6…│
├──┬────────────┬──────────────┬──────────────┬──────────┬───────────┤
│  │ DAY        │       SONNET │         OPUS │    EST $  │  CLIENT $ │
├──┼────────────┼──────────────┼──────────────┼──────────┼───────────┤
│● │ 2026-06-16 │  $4.12(900k) │ $11.80(420k) │  $15.92  │   $23.88  │
│  │ 2026-06-15 │  $2.01(510k) │  $6.40(210k) │   $8.41  │   $12.62  │
│● │ 2026-06-14 │  $9.55(2.1M) │       —      │   $9.55  │   $14.33  │
└──┴────────────┴──────────────┴──────────────┴──────────┴───────────┘
 e edit margin   space mark   y copy CSV   r refresh   q quit
```

---

## The premise: an estimate, not your receipt

Lots of people (maybe you) do client work on a **Claude Code subscription** — a flat monthly
fee, not metered per token. But clients are often billed **by usage**. That leaves a gap: you
*used* the model heavily on a client's project, but you have no per-project dollar figure to put
on an invoice, because your subscription bill is one flat number.

`cctab` fills that gap. It answers:

> *"What would this work have cost at Anthropic's published API rates?"*

That number is useful in two ways:

1. **Client billing today** — a defensible, itemized, per-day breakdown you can hand a client or
   drop into an invoice, marked up by whatever margin you've agreed.
2. **Continuity insurance** — if your subscription is ever suspended, rate-limited, or you simply
   decide to move a client onto the metered API, you already know the real cost shape of the work
   and can quote it without guessing.

> [!IMPORTANT]
> **`cctab` does not know what Anthropic actually charged you.** You pay a flat subscription;
> there is no per-token receipt to read. `cctab` takes your *real token counts* (which Claude
> Code records exactly) and multiplies them by **public API list rates**. The `EST $` column is
> therefore an **API-equivalent estimate**, not a copy of an invoice. Token counts are exact;
> dollars are a model. For an authoritative metered figure, use
> [`ccusage`](https://github.com/ryoppippi/ccusage).

---

## How it works (in one paragraph)

Claude Code stores one folder per working directory under
`~/.claude/projects/<encoded-cwd>/`, with one `*.jsonl` transcript per session. Every assistant
message in those transcripts carries a `message.usage` block with exact input / output / cache
token counts and the `model` id. `cctab` scans those files, buckets the tokens by **local
calendar day** and **model family** (Haiku, Sonnet, Opus, Fable), prices each bucket with that
family's own `$/MTok` rates, and shows you one row per day. It is **read-only** — it never writes
to, deletes, or moves anything under `~/.claude`, and it makes no network calls. The only file it
ever writes is the small `.cctop` margin file in your launch directory, and only when you edit
the margin in-app.

---

## Install & run

Requires [`uv`](https://docs.astral.sh/uv/) (recommended) or any Python ≥ 3.9.

```bash
# Run from the project you want to bill — no install, uv resolves deps ephemerally.
# The view is ALWAYS scoped to the directory you launch from.
cd ~/Projects/some-client-project
uv run --project ~/Projects/cctop cctab
```

Or install it once as a tool and run `cctab` anywhere:

```bash
uv tool install ~/Projects/cctop
cd ~/Projects/some-client-project
cctab
```

Without `uv`:

```bash
cd ~/Projects/cctop
python -m venv .venv && . .venv/bin/activate
pip install -e .
cctab        # then cd into a project and run it there
```

> [!NOTE]
> **Naming.** The PyPI distribution and the command are **`cctab`** (the unhyphenated `cctop`
> was already taken on PyPI by an unrelated project). The Python import package is still `cctop`,
> and the config identifiers below keep the `cctop` / `CCTOP_` prefix — so any existing `.cctop`
> files and `CCTOP_*` environment variables keep working unchanged. If you previously installed
> the old name, run `uv tool install --force ~/Projects/cctop` to pick up the `cctab` command.

---

## Scope: it always bills the directory you launch from

`cctab` is **always scoped to your current working directory** — there is no global leaderboard
view. Launch it from the project you want to bill:

- Sessions whose transcript `cwd` is the launch directory are included.
- So are sessions from any directory **nested beneath** it — this deliberately folds in the
  project's `.claude/worktrees/*` and `.claude/agents` sessions, so subagent and worktree usage
  is billed to the project that spawned it rather than disappearing.
- Everything else is excluded.

If you launch from a directory with no transcripts, you'll see `no transcripts for <dir>`.

---

## The daily table

One row per **local calendar day** (newest first; an `unknown` row, for any line missing a
parseable timestamp, always sorts last). The columns:

| Column | What it shows |
|--------|---------------|
| `●` (mark) | A green `●` when the day is marked for CSV export (see [Billing](#billing-workflow-mark--copy)). |
| `DAY` | The local calendar day, `YYYY-MM-DD`. |
| `HAIKU` / `SONNET` / `OPUS` / `FABLE` | Per-family usage for that day as **`$cost(tokens)`**, e.g. `$4.12(900k)`. Blank/`—` when a family wasn't used that day. Tokens are abbreviated (`k`/`M`/`B`); the dollar figure is that family's exact priced cost. |
| `EST $` | The day's **API-equivalent cost** — the sum of every family's cost. This is your raw billable number. |
| `CLIENT $` | `EST $ × margin` — what you'd actually charge the client after your markup. |

**Color heat** on the `EST $` / `CLIENT $` cells is a quick visual scale, not data:
`dim` < \$5 · `green` ≥ \$5 · `yellow` ≥ \$20 · `bold red` ≥ \$100 per day.

### Totals bar

The bar across the top summarizes the whole visible (scoped) range:

```
cwd: ~/Projects/wbm-booking · 6 days   4.2M tok   $58.41 est   $87.62 client   ·  margin:1.5 (.cctop)
```

…showing the scoped directory, the day count, total tokens, total `EST $`, total `CLIENT $`, and
the **active margin with its source** (`.cctop`, `env`, or `unset`). If a margin write failed,
it appends `(could not write .cctop)`.

---

## Keys

| Key | Action |
|-----|--------|
| `r` | Rescan the transcripts (after more Claude Code activity). |
| `e` | Edit the client margin (opens an input on the daily view). |
| `escape` | Cancel the margin edit without changing anything. |
| `space` | Mark / unmark the current day for export. |
| `y` | Copy marked days — or the whole table if none are marked — as CSV. |
| `q` | Quit. |

---

## Billing workflow: mark → copy

1. Launch `cctab` from the client's project directory.
2. Press `space` on each day you want to invoice — a green `●` appears. (Mark nothing to export
   the entire visible range.)
3. Press `y`. The marked days are copied to your clipboard as CSV, and a toast confirms how many.

### What lands on your clipboard

A header row, one row per day, and a `TOTAL` row that sums every numeric column. Numbers are
**raw and spreadsheet-clean** — integer token counts, 2-decimal dollars, no `$` signs, no `k`/`M`
abbreviation — so every column is directly `SUM()`-able when pasted:

```csv
day,haiku_tokens,haiku_cost,sonnet_tokens,sonnet_cost,opus_tokens,opus_cost,fable_tokens,fable_cost,est_usd,client_usd
2026-06-16,0,0.00,900000,4.12,420000,11.80,0,0.00,15.92,23.88
2026-06-14,0,0.00,2100000,9.55,0,0.00,0,0.00,9.55,14.33
TOTAL,0,0.00,3000000,13.67,420000,11.80,0,0.00,25.47,38.21
```

### Clipboard reliability

Copy goes to your **real OS clipboard** via the platform tool — `pbcopy` (macOS), `clip`
(Windows), or `wl-copy` / `xclip` / `xsel` (Linux) — with a terminal **OSC 52** sequence as a
fallback. This matters because many terminals (Terminal.app, bare `tmux` without
`set-clipboard on`) silently drop OSC 52, so the keypress *looks* like it worked but nothing
reaches the clipboard. If the toast says *"sent … to terminal clipboard — paste to check"*, no
native tool was found and `cctab` fell back to OSC 52 — verify the paste.

---

## Client margin

`CLIENT $ = EST $ × margin`, where `margin` is a markup multiplier (e.g. `1.5` = a 50% markup;
`1.0` = bill at cost). The margin is resolved per launch with this **precedence**:

1. **`<launch dir>/.cctop`** — a per-directory JSON file, written when you edit the margin in-app.
2. **`CCTOP_MARGIN`** env var — a global default for directories with no `.cctop`.
3. **`1.0`** — no markup (`CLIENT $` equals `EST $`).

### The `.cctop` file

A tiny JSON file at the root of the directory you launch from:

```json
{"margin": 1.5}
```

- `cctab` **reads** it on launch and applies it to every `CLIENT $`.
- It is **created only when you edit the margin in-app** — merely launching `cctab` never creates
  it.
- A missing, malformed, or out-of-range value (non-object, non-number, negative, `inf`, `NaN`, or
  a numeric *string*) is silently ignored and falls through to the next precedence source.
- Writes are **atomic** (temp file + `os.replace`), so a crash can't corrupt it.

Because `.cctop` holds a client rate that usually differs per relationship, consider git-ignoring
it:

```gitignore
.cctop
```

### Editing the margin in-app

Press `e`, type a new multiplier (e.g. `1.3`), and press `Enter` — `cctab` writes it to
`<launch dir>/.cctop` immediately and re-renders. `escape` cancels. An invalid entry
(non-numeric, negative, `inf`) is ignored with no write. Set a global default without a file via:

```bash
CCTOP_MARGIN=1.3 cctab
```

---

## The cost model

`EST $` is computed **per model family** — each family is priced from its own `$/MTok` rates and
its own four token classes, then summed. There is no single blended rate.

### Default rates ($/MTok)

| Family | Input | Output | Cache write | Cache read |
|--------|------:|-------:|------------:|-----------:|
| Haiku  |  1.00 |   5.00 |        1.25 |       0.10 |
| Sonnet |  3.00 |  15.00 |        3.75 |       0.30 |
| Opus   |  5.00 |  25.00 |        6.25 |       0.50 |
| Fable  | 10.00 |  50.00 |       12.50 |       1.00 |

The four token classes come straight from each message's `usage` block: **input**, **output**,
**cache write** (`cache_creation_input_tokens`), and **cache read**
(`cache_read_input_tokens`). Cache reads are roughly a tenth of input price — which is exactly why
a raw token total badly overstates cost, and why `cctab` prices each class separately.

### How a model id maps to a family

The `model` id on each message is matched **case-insensitively, by substring, in priority order**
`opus → sonnet → haiku → fable`. The literal `<synthetic>` model maps to a zero-cost `synthetic`
family (so synthetic/test traffic never inflates a bill). Anything unrecognized — or missing —
falls back to a `default` family priced at the blended `CCTOP_RATE_*` rates below.

### Overriding rates

Every rate is env-overridable in `$/MTok`. Per-family overrides take the form
`CCTOP_RATE_<FAMILY>_<CLASS>`:

```
CCTOP_RATE_HAIKU_INPUT        CCTOP_RATE_HAIKU_OUTPUT
CCTOP_RATE_HAIKU_CACHE_WRITE  CCTOP_RATE_HAIKU_CACHE_READ

CCTOP_RATE_SONNET_INPUT       CCTOP_RATE_SONNET_OUTPUT
CCTOP_RATE_SONNET_CACHE_WRITE CCTOP_RATE_SONNET_CACHE_READ

CCTOP_RATE_OPUS_INPUT         CCTOP_RATE_OPUS_OUTPUT
CCTOP_RATE_OPUS_CACHE_WRITE   CCTOP_RATE_OPUS_CACHE_READ

CCTOP_RATE_FABLE_INPUT        CCTOP_RATE_FABLE_OUTPUT
CCTOP_RATE_FABLE_CACHE_WRITE  CCTOP_RATE_FABLE_CACHE_READ
```

The blended-rate overrides `CCTOP_RATE_INPUT`, `CCTOP_RATE_OUTPUT`, `CCTOP_RATE_CACHE_WRITE`, and
`CCTOP_RATE_CACHE_READ` (defaults `3.00 / 15.00 / 3.75 / 0.30`) apply to the `default` family —
i.e. any model whose family isn't one of the four named above.

---

## Configuration reference

| Setting | Type | Default | Effect |
|---------|------|---------|--------|
| `CLAUDE_PROJECTS_DIR` | env | `~/.claude/projects` | Where to read transcripts from. |
| `CCTOP_MARGIN` | env | `1.0` | Global client markup multiplier (overridden by a `.cctop` file). |
| `CCTOP_RATE_<FAMILY>_<CLASS>` | env | see table | Per-family `$/MTok` rate override. |
| `CCTOP_RATE_<CLASS>` | env | `3 / 15 / 3.75 / 0.30` | Blended rate for the `default` family. |
| `<launch dir>/.cctop` | file | — | Per-directory margin, `{"margin": <number>}`; written on in-app edit. |

---

## Accuracy & limitations

- **Token counts are exact**; dollar figures are an estimate at list rates and won't match a real
  metered invoice to the cent (no discounts, batch pricing, or plan adjustments are modeled).
- **It can't see your subscription bill.** `EST $` is a re-pricing, by design — see
  [the premise](#the-premise-an-estimate-not-your-receipt).
- **Parsing is best-effort.** Unreadable files and malformed JSON lines are skipped silently so
  one bad transcript never breaks a scan; a line missing token fields contributes nothing.
- **Days are local-time.** Timestamps are converted from UTC to your machine's local day; lines
  with no parseable timestamp land in the `unknown` row.
- **Read-only.** `cctab` never modifies your Claude Code transcripts and makes no network calls.
  The sole write is the `.cctop` margin file, only on an in-app margin edit.
- For an authoritative metered cost, cross-check with
  [`ccusage`](https://github.com/ryoppippi/ccusage).

---

## Development

```bash
uv sync                       # install deps (incl. dev: pytest, ruff)
uv run pytest                 # run the test suite
uv run ruff check .           # lint
```

The codebase is two modules with a strict one-way dependency:

- **`src/cctop/data.py`** — the foundation layer: transcript parsing, per-day/per-family
  aggregation, the `$/MTok` rate table, and cost math. Pure, no UI. Never imports from `app`.
- **`src/cctop/app.py`** — the [Textual](https://textual.textualize.io/) TUI: the daily screen,
  key bindings, table rendering, the CSV exporter, and the `human` / `cost_cell` / `model_cell`
  formatting helpers. Imports *from* `data.py`, never the reverse.

All `$/MTok` rates live only in `data.py`; all number/`$` formatting lives only in `app.py`.
