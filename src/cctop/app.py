"""cctop — a Textual TUI over Claude Code token usage."""

from __future__ import annotations

import math
import os

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


# ---------------------------------------------------------------------------
# DailyScreen — one row per day, one column per model family
# ---------------------------------------------------------------------------


class DailyScreen(Screen):
    """Daily token & cost view, per model family, cwd-scoped."""

    BINDINGS = [
        Binding("e", "edit_margin", "Edit margin"),
        Binding("escape", "cancel_margin", "Cancel", show=False),
    ]

    CSS = """
    #summary { dock: top; height: 1; padding: 0 1; background: $boost; }
    DataTable { height: 1fr; }
    #margin-input { dock: bottom; height: 3; display: none; }
    #margin-input.visible { display: block; }
    """

    def compose(self) -> ComposeResult:
        yield Static("scanning…", id="summary")
        table: DataTable = DataTable(zebra_stripes=True, id="daily-table")
        table.cursor_type = "row"
        yield table
        yield Input(id="margin-input", placeholder="")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#daily-table", DataTable)
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
        table.clear()

        agg_cost = 0.0
        agg_tokens = 0
        for d in days:
            agg_cost += d.cost
            agg_tokens += d.total
            cells: list[Text | str] = [d.day]
            for fam in FAMILIES:
                usage = d.by_model.get(fam)
                cells.append(model_cell(usage, fam))
            cells.append(cost_cell(d.cost))
            cells.append(cost_cell(d.client))
            table.add_row(*cells)

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

    def action_edit_margin(self) -> None:
        """Reveal the margin input and focus it (e binding)."""
        margin_input = self.query_one("#margin-input", Input)
        margin_input.placeholder = str(current_margin())
        margin_input.value = ""
        margin_input.add_class("visible")
        margin_input.focus()

    def action_cancel_margin(self) -> None:
        """Hide the margin input without making changes (escape binding)."""
        margin_input = self.query_one("#margin-input", Input)
        margin_input.remove_class("visible")
        self.query_one("#daily-table", DataTable).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle margin submission from #margin-input."""
        if event.input.id != "margin-input":
            return
        margin_input = self.query_one("#margin-input", Input)
        margin_input.remove_class("visible")
        self.query_one("#daily-table", DataTable).focus()

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
