# cctop

An `htop`-style terminal UI for **Claude Code token usage — daily per-model cost, per directory**.

Claude Code stores one folder per working directory under
`~/.claude/projects/<encoded-cwd>/`, with a `*.jsonl` transcript per session.
`cctop` reads those logs and shows a **daily breakdown for the current directory**:
one row per day, one column per model family (Haiku, Sonnet, Opus, Fable) with
`$cost(tokens)`, plus per-day `EST $` (your cost) and `CLIENT $` (cost × margin)
totals. Cost is **per-model accurate** — each family priced from its own $/MTok
rates. Launch from any project directory to scope the view to that project; pass
`--global` to see all directories combined.

## Features

- **Daily view** (default) — one row per calendar day (local time), columns per
  model family, `EST $` and `CLIENT $` totals per day.
- **Project leaderboard** (`p`) — the per-directory token/cost leaderboard,
  switchable at runtime. Press `d` to return to the daily view.
- **cwd / global scope** (`g`) — toggles between the launch directory and all
  directories without restarting.
- **Merge by cwd** (`m`, in projects view) — collapses worktrees and duplicate
  encoded folders that resolve to the same directory into one row.
- **Live filter** (`/`, in projects view) — type to narrow to matching paths.
- **Drill-down** — press <kbd>Enter</kbd> on a project row to see its per-session
  breakdown.
- **Totals bar** — scope, day count, tokens and est. cost for whatever is in view.

## Run

Requires [`uv`](https://docs.astral.sh/uv/) (recommended) or any Python ≥ 3.9.

```bash
# from anywhere, no install — uv resolves deps into an ephemeral env
uv run --project ~/Projects/cctop cctop

# scope to all directories
uv run --project ~/Projects/cctop cctop --global   # or -g

# or install it as a tool, then just run `cctop`
uv tool install ~/Projects/cctop
cctop
cctop --global
```

Without uv:

```bash
cd ~/Projects/cctop
python -m venv .venv && . .venv/bin/activate
pip install -e .
cctop
cctop --global
```

## Keys

| key | action |
|-----|--------|
| `d` | switch to daily view |
| `p` | switch to projects leaderboard |
| `g` | toggle cwd / global scope |
| `r` | rescan |
| `q` | quit |
| `t` / `c` / `o` / `n` | sort by total / cost / output / name *(projects view)* |
| `m` | toggle merge-by-cwd *(projects view)* |
| `/` | filter; `esc` clears *(projects view)* |
| `enter` | drill into selected project's sessions *(projects view)* |

## Cost & rates

`EST $` uses **per-model public list rates** ($/MTok):

| Family | Input | Output | Cache write | Cache read |
|--------|------:|-------:|------------:|-----------:|
| Haiku  |  1.00 |   5.00 |        1.25 |       0.10 |
| Sonnet |  3.00 |  15.00 |        3.75 |       0.30 |
| Opus   | 15.00 |  75.00 |       18.75 |       1.50 |
| Fable  | 15.00 |  75.00 |       18.75 |       1.50 |

Token counts are exact. For authoritative cost use `ccusage`.

Override any rate via env vars ($/MTok):

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

The legacy blended-rate overrides (`CCTOP_RATE_INPUT`, `CCTOP_RATE_OUTPUT`,
`CCTOP_RATE_CACHE_WRITE`, `CCTOP_RATE_CACHE_READ`) still apply to any model whose
family is not recognised (falls back to the `default` family).

## Client margin

`CLIENT $` = `EST $` × `CCTOP_MARGIN` (a markup multiplier, default `1.0` so
`CLIENT $` equals `EST $` when unset). Set `CCTOP_MARGIN=1.3` to add 30% markup:

```bash
CCTOP_MARGIN=1.3 cctop
```

## Log root

Point at a different log root with `CLAUDE_PROJECTS_DIR` (default `~/.claude/projects`).
