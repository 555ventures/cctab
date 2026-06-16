# cctop

An `htop`-style terminal UI for **Claude Code token usage — daily per-model cost, per directory**.

Claude Code stores one folder per working directory under
`~/.claude/projects/<encoded-cwd>/`, with a `*.jsonl` transcript per session.
`cctop` reads those logs and shows a **daily breakdown for the current directory**:
one row per day, one column per model family (Haiku, Sonnet, Opus, Fable) with
`$cost(tokens)`, plus per-day `EST $` (your cost) and `CLIENT $` (cost × margin)
totals. Cost is **per-model accurate** — each family priced from its own $/MTok
rates. The view is **always scoped to the directory you launch cctop from**
(folding in its worktrees and `.claude` subdirs) — launch from the project you
want to bill.

## Features

- **Daily view** — one row per calendar day (local time), columns per model
  family, `EST $` and `CLIENT $` totals per day, scoped to the launch directory.
- **Totals bar** — directory, day count, tokens, est. and client cost.

## Run

Requires [`uv`](https://docs.astral.sh/uv/) (recommended) or any Python ≥ 3.9.

```bash
# from the project you want to bill, no install — uv resolves deps ephemerally
uv run --project ~/Projects/cctop cctop

# or install it as a tool, then just run `cctop`
uv tool install ~/Projects/cctop
cctop
```

Without uv:

```bash
cd ~/Projects/cctop
python -m venv .venv && . .venv/bin/activate
pip install -e .
cctop
```

## Keys

| key      | action                              |
|----------|-------------------------------------|
| `r`      | rescan                              |
| `e`      | edit client margin (daily view)     |
| `escape` | cancel margin edit (daily view)     |
| `q`      | quit                                |

## Cost & rates

`EST $` uses **per-model public list rates** ($/MTok):

| Family | Input | Output | Cache write | Cache read |
|--------|------:|-------:|------------:|-----------:|
| Haiku  |  1.00 |   5.00 |        1.25 |       0.10 |
| Sonnet |  3.00 |  15.00 |        3.75 |       0.30 |
| Opus   |  5.00 |  25.00 |        6.25 |       0.50 |
| Fable  | 10.00 |  50.00 |       12.50 |       1.00 |

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

`CLIENT $` = `EST $` × margin (a markup multiplier). Each directory carries its
own margin in a `.cctop` file at the **launch directory** root — no env variable needed.

### Per-directory `.cctop` file

Create (or let cctop create) a `.cctop` file in the project directory you launch from:

```json
{"margin": 2.0}
```

cctop reads it on launch and applies it to every `CLIENT $` figure. The file is
created **only when you edit the margin in-app** — launching cctop never creates it.
A missing, malformed, or out-of-range value silently falls back to the next source.

### Editing the margin in-app

| key      | action                                    |
|----------|-------------------------------------------|
| `e`      | open the margin editor (on the daily view)|
| `escape` | cancel without changing the margin        |

Press `e`, type a new multiplier (e.g. `1.3`), and press Enter. cctop writes the new
value to `<launch dir>/.cctop` immediately. An invalid entry (non-numeric, negative,
`inf`) is ignored with no write.

The totals bar shows the active margin and its source, e.g. `2.0 (.cctop)`,
`1.3 (env)`, or `1.0 (unset)`.

### Precedence

1. `<launch dir>/.cctop` — per-directory, written on in-app edit.
2. `CCTOP_MARGIN` env var — global default for all directories.
3. `1.0` — no markup (CLIENT $ = EST $).

```bash
# global default via env (used when no .cctop present)
CCTOP_MARGIN=1.3 cctop
```

### `.gitignore`

`.cctop` holds a client rate that typically varies per billing relationship. Consider
adding it to your `.gitignore`:

```
.cctop
```

## Log root

Point at a different log root with `CLAUDE_PROJECTS_DIR` (default `~/.claude/projects`).
