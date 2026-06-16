"""cctop — a Textual TUI over Claude Code token usage."""

from __future__ import annotations

import csv
import io
import math
import os
import shutil
import subprocess
import sys

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static

import cctop.data as _data
from cctop.data import (
    FAMILIES,
    DayUsage,
    Usage,
    client_cost,
    cost_of,
    current_margin,
    read_dir_margin,
    scan_daily,
    set_margin,
    shorten,
)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def human(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}k"
    return str(int(n))


def cost_cell(c: float) -> Text:
    s = f"${c:,.2f}"
    if c >= 100:
        style = "bold red"
    elif c >= 20:
        style = "yellow"
    elif c >= 5:
        style = "green"
    else:
        style = "dim"
    return Text(s, style=style, justify="right")


def model_cell(usage: object | None, family: str) -> Text:
    """Render a model-column cell as '$cost(tokens)', dim when zero.

    Reuses human() for the token part and data.cost_of for the dollar part —
    no rate literal, no re-derived formatting.
    """
    if usage is None:
        return Text("", style="dim", justify="right")
    if not isinstance(usage, Usage):
        return Text("", style="dim", justify="right")
    dollars = cost_of(usage, family)
    tokens = usage.total
    if tokens == 0 and dollars == 0.0:
        return Text("", style="dim", justify="right")
    s = f"${dollars:,.2f}({human(tokens)})"
    return Text(s, style="grey70", justify="right")


def system_clipboard_copy(text: str) -> bool:
    """Best-effort copy to the OS clipboard via a platform CLI. Returns True on success.

    cctop's other clipboard path is OSC 52 (``App.copy_to_clipboard``), which many
    terminals silently drop (Terminal.app has no OSC 52 support; tmux blocks it without
    ``set-clipboard on``), so the keypress appears to work but nothing reaches the
    clipboard. As a fallback we shell out to the platform clipboard tool. Best-effort:
    any failure (tool missing, non-zero exit, OSError) returns False and the caller keeps
    the OSC 52 path. Never raises, never writes to stdout (the TUI owns the screen).
    """
    if sys.platform == "darwin":
        candidates = [["pbcopy"]]
    elif sys.platform == "win32":
        candidates = [["clip"]]
    else:
        candidates = [
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            proc = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            continue
        if proc.returncode == 0:
            return True
    return False


def daily_csv(days: list[DayUsage]) -> str:
    """Serialize day rows as billing CSV (D4/D5/D6).

    Columns: day, then <fam>_tokens/<fam>_cost per FAMILIES, then est_usd/client_usd;
    a trailing TOTAL row sums each numeric column. Numbers are raw — tokens as integers,
    dollars as 2-decimal floats (no `$`, no abbreviation) — for direct spreadsheet paste.
    Per-family cost via data.cost_of (no rate literal here); a family absent from a day's
    by_model contributes 0 tokens / 0.00 cost (never cost_of(None, …), which would raise).
    """
    header = ["day"]
    for fam in FAMILIES:
        header.append(f"{fam}_tokens")
        header.append(f"{fam}_cost")
    header.append("est_usd")
    header.append("client_usd")

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)

    fam_tok_totals = {fam: 0 for fam in FAMILIES}
    fam_cost_totals = {fam: 0.0 for fam in FAMILIES}
    est_total = 0.0
    client_total = 0.0

    for d in days:
        row: list[object] = [d.day]
        for fam in FAMILIES:
            u = d.by_model.get(fam)
            toks = u.total if u else 0
            cost = cost_of(u, fam) if u else 0.0
            row.append(toks)
            row.append(f"{cost:.2f}")
            fam_tok_totals[fam] += toks
            fam_cost_totals[fam] += cost
        row.append(f"{d.cost:.2f}")
        row.append(f"{d.client:.2f}")
        est_total += d.cost
        client_total += d.client
        writer.writerow(row)

    total_row: list[object] = ["TOTAL"]
    for fam in FAMILIES:
        total_row.append(fam_tok_totals[fam])
        total_row.append(f"{fam_cost_totals[fam]:.2f}")
    total_row.append(f"{est_total:.2f}")
    total_row.append(f"{client_total:.2f}")
    writer.writerow(total_row)

    return buf.getvalue()


# ---------------------------------------------------------------------------
# DailyScreen — one row per day, one column per model family
# ---------------------------------------------------------------------------


class DailyScreen(Screen):
    """Daily token & cost view, per model family, cwd-scoped."""

    BINDINGS = [
        Binding("e", "edit_margin", "Edit margin"),
        Binding("escape", "cancel_margin", "Cancel", show=False),
        Binding("space", "toggle_select", "Mark"),
        Binding("y", "copy_csv", "Copy CSV"),
    ]

    CSS = """
    #summary { dock: top; height: 1; padding: 0 1; background: $boost; }
    DataTable { height: 1fr; }
    #margin-input { dock: bottom; height: 3; }
    """

    def __init__(self) -> None:
        super().__init__()
        # Day keys (d.day) marked for the CSV billing export (D1).
        self.selected: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Static("scanning…", id="summary")
        table: DataTable = DataTable(zebra_stripes=True, id="daily-table")
        table.cursor_type = "row"
        yield table
        # The margin Input is mounted lazily in action_edit_margin — mounting it at
        # compose time crashes the real driver: Input._watch_selection fires during
        # _post_mount and calls App.clear_selection(), which queries self.screen
        # before the MODES auto-mount has pushed this screen onto the stack
        # (ScreenStackError, uncaught — Textual only guards NoScreen there).
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#daily-table", DataTable)
        table.add_column("", key="mark", width=2)
        table.add_column("DAY", key="day", width=12)
        for fam in FAMILIES:
            table.add_column(Text(fam.upper(), justify="right"), key=fam, width=18)
        table.add_column(Text("EST $", justify="right"), key="est")
        table.add_column(Text("CLIENT $", justify="right"), key="client")

    def refresh_daily(self) -> None:
        """Re-render the table from app.days."""
        app: CCTop = self.app  # type: ignore[assignment]
        days: list[DayUsage] = app.days
        table = self.query_one("#daily-table", DataTable)
        # table.clear() resets the cursor to row 0; remember where it was so a rescan
        # (or a future full refresh) doesn't snap the selection cursor back to the top.
        prev_cursor = table.cursor_row
        table.clear()

        # Drop any marked days that vanished after a rescan (D7).
        self.selected &= {d.day for d in days}

        agg_cost = 0.0
        agg_tokens = 0
        for d in days:
            agg_cost += d.cost
            agg_tokens += d.total
            marker = Text("●", style="bold green") if d.day in self.selected else Text("")
            cells: list[Text | str] = [marker, d.day]
            for fam in FAMILIES:
                usage = d.by_model.get(fam)
                cells.append(model_cell(usage, fam))
            cells.append(cost_cell(d.cost))
            cells.append(cost_cell(d.client))
            table.add_row(*cells, key=d.day)

        # Restore the cursor row (clamped) so rebuilding the table doesn't lose the user's place.
        if table.row_count:
            table.move_cursor(row=min(prev_cursor, table.row_count - 1))

        scope_cwd = app.scope_cwd
        scope_label = f"cwd: {shorten(scope_cwd)}"
        margin_val = app._margin_label()

        if not days:
            summary_text = Text(f"no transcripts for {shorten(scope_cwd)}", "dim")
        else:
            summary_text = Text.assemble(
                (scope_label, "bold"),
                (f" · {len(days)} days", "dim"),
                ("   "),
                (f"{human(agg_tokens)} tok", "bold white"),
                ("   "),
                (f"${agg_cost:,.2f} est", "bold yellow"),
                ("   "),
                (f"${client_cost(agg_cost):,.2f} client", "green"),
                (f"   ·  margin:{margin_val}", "dim"),
            )
        self.query_one("#summary", Static).update(summary_text)

    def on_show(self) -> None:
        """Re-render when this screen becomes active."""
        self.refresh_daily()

    async def action_edit_margin(self) -> None:
        """Mount the margin input and focus it (e binding).

        Mounted lazily (not in compose) so the selection watcher fires only after
        this screen is on the stack — see compose() for the boot-time crash this avoids.
        """
        if self.query("#margin-input"):
            return
        margin_input = Input(id="margin-input", placeholder=str(current_margin()))
        await self.mount(margin_input)
        margin_input.focus()

    def _close_margin_input(self) -> None:
        """Remove the margin input (if mounted) and return focus to the table."""
        for margin_input in self.query("#margin-input"):
            margin_input.remove()
        self.query_one("#daily-table", DataTable).focus()

    def action_cancel_margin(self) -> None:
        """Discard the margin input without making changes (escape binding)."""
        self._close_margin_input()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle margin submission from #margin-input."""
        if event.input.id != "margin-input":
            return
        self._close_margin_input()

        raw = event.value.strip()
        if not raw:
            return

        app: CCTop = self.app  # type: ignore[assignment]
        try:
            v = float(raw)
        except ValueError:
            return

        if not (math.isfinite(v) and v >= 0):
            return

        set_margin(v)
        ok = _data.write_dir_margin(app._launch_cwd, v)
        app._margin_source = ".cctop"
        if not ok:
            app._write_failed = True
        else:
            app._write_failed = False
        self.refresh_daily()

    def action_toggle_select(self) -> None:
        """Toggle the cursor row's day in/out of the marked set (space binding)."""
        app: CCTop = self.app  # type: ignore[assignment]
        table = self.query_one("#daily-table", DataTable)
        i = table.cursor_row
        if not (0 <= i < len(app.days)):
            return
        day = app.days[i].day
        if day in self.selected:
            self.selected.discard(day)
        else:
            self.selected.add(day)
        # Update only the marker cell — a full refresh_daily() would clear() the table and
        # snap the cursor back to row 0, breaking sequential multi-select.
        marker = Text("●", style="bold green") if day in self.selected else Text("")
        table.update_cell(day, "mark", marker)

    def action_copy_csv(self) -> None:
        """Copy marked days (or all visible) to the clipboard as CSV (y binding)."""
        app: CCTop = self.app  # type: ignore[assignment]
        if self.selected:
            rows = [d for d in app.days if d.day in self.selected]
        else:
            rows = list(app.days)
        text = daily_csv(rows)
        # OSC 52 path (works in terminals that support it), plus a best-effort native
        # clipboard write (pbcopy/xclip/clip) since many terminals drop OSC 52.
        self.app.copy_to_clipboard(text)
        native = system_clipboard_copy(text)
        if native:
            msg = f"copied {len(rows)} day(s) to clipboard (CSV)"
        else:
            msg = f"sent {len(rows)} day(s) to terminal clipboard (CSV) — paste to check"
        self.app.notify(msg, timeout=3)


# ---------------------------------------------------------------------------
# Root App — single-mode controller
# ---------------------------------------------------------------------------


class CCTop(App):
    """Claude Code token usage, top-style."""

    MODES = {
        "daily": DailyScreen,
    }
    DEFAULT_MODE = "daily"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, launch_cwd: str | None = None) -> None:
        super().__init__()
        self.days: list[DayUsage] = []
        # _launch_cwd: captured once at construction, never re-read
        self._launch_cwd: str = launch_cwd or os.getcwd()
        # Always scoped to the launch cwd — global scope is gone.
        self.scope_cwd: str = self._launch_cwd
        # Margin source: ".cctop", "env", or "unset"
        self._margin_source: str = "unset"
        # Tracks whether the last write_dir_margin call failed.
        self._write_failed: bool = False

    def _margin_label(self) -> str:
        m = current_margin()
        label = f"{m} ({self._margin_source})"
        if self._write_failed:
            label += " (could not write .cctop)"
        return label

    def on_mount(self) -> None:
        self.title = "cctop"
        self.sub_title = "Claude Code token usage"
        # Capture env/default margin before reading .cctop (D3).
        env_margin = current_margin()
        m = read_dir_margin(self._launch_cwd)
        set_margin(m if m is not None else env_margin)
        if m is not None:
            self._margin_source = ".cctop"
        elif env_margin != 1.0:
            self._margin_source = "env"
        else:
            self._margin_source = "unset"
        self.load_data()

    # ---- data loading (threaded) -------------------------------------------

    @work(thread=True, exclusive=True)
    def load_data(self) -> None:
        days = scan_daily(cwd=self._launch_cwd)
        self.call_from_thread(self._on_loaded, days)

    def _on_loaded(self, days: list[DayUsage]) -> None:
        self.days = days
        screen = self.screen
        if isinstance(screen, DailyScreen):
            screen.refresh_daily()

    # ---- actions ------------------------------------------------------------

    def action_refresh(self) -> None:
        # Update the active screen's summary to "scanning…"
        screen = self.screen
        try:
            screen.query_one("#summary", Static).update("scanning…")
        except Exception:
            pass
        self.load_data()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    CCTop().run()


if __name__ == "__main__":
    main()
