"""cctop — a Textual TUI over Claude Code token usage."""

from __future__ import annotations

import os
import sys

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, Static

from cctop.data import (
    FAMILIES,
    DayUsage,
    Project,
    Session,
    cost_of,
    scan,
    scan_daily,
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


def num_cell(n: int, *, heat: float = 0.0) -> Text:
    """Right-aligned number, brightened by `heat` in [0, 1]."""
    if heat >= 0.66:
        style = "bold white"
    elif heat >= 0.33:
        style = "white"
    elif n == 0:
        style = "dim"
    else:
        style = "grey70"
    return Text(human(n), style=style, justify="right")


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
    from cctop.data import Usage as _Usage

    if usage is None:
        return Text("", style="dim", justify="right")
    assert isinstance(usage, _Usage)
    dollars = cost_of(usage, family)
    tokens = usage.total
    if tokens == 0 and dollars == 0.0:
        return Text("", style="dim", justify="right")
    s = f"${dollars:,.2f}({human(tokens)})"
    return Text(s, style="grey70", justify="right")


# ---------------------------------------------------------------------------
# Projects-screen columns / sort (same as the original leaderboard)
# ---------------------------------------------------------------------------

# Column key -> (key, header)
COLUMNS: list[tuple[str, str]] = [
    ("project", "PROJECT"),
    ("sessions", "SESS"),
    ("input", "INPUT"),
    ("output", "OUTPUT"),
    ("cache_create", "CACHE W"),
    ("cache_read", "CACHE R"),
    ("total", "TOTAL"),
    ("cost", "EST $"),
]

SORTABLE = {
    "sessions": lambda p: p.usage.sessions,
    "input": lambda p: p.usage.input,
    "output": lambda p: p.usage.output,
    "cache_create": lambda p: p.usage.cache_create,
    "cache_read": lambda p: p.usage.cache_read,
    "total": lambda p: p.usage.total,
    "cost": lambda p: p.cost,          # accurate per-family cost (D3)
    "project": lambda p: p.key.lower(),
}


# ---------------------------------------------------------------------------
# Drill-down modal (unchanged structure; EST $ uses accurate s.cost / proj.cost)
# ---------------------------------------------------------------------------


class SessionsScreen(ModalScreen):
    """Drill-down: per-session breakdown for one project."""

    BINDINGS = [
        Binding("escape,q,enter", "dismiss", "Back"),
    ]

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project

    def compose(self) -> ComposeResult:
        with Vertical(id="drill"):
            u = self.project.usage
            yield Static(
                Text.assemble(
                    ("  ", ""),
                    (shorten(self.project.key), "bold cyan"),
                    ("   ", ""),
                    (f"{u.sessions} sessions  ", "dim"),
                    (f"{human(u.total)} tok  ", "white"),
                    (f"${self.project.cost:,.2f}", "yellow"),
                ),
                id="drill-title",
            )
            table: DataTable = DataTable(id="drill-table", zebra_stripes=True)
            table.cursor_type = "row"
            yield table
            yield Static(
                Text("  esc / q  back", style="dim"), id="drill-hint"
            )

    def on_mount(self) -> None:
        table = self.query_one("#drill-table", DataTable)
        table.add_column("SESSION", width=20)
        for h in ("INPUT", "OUTPUT", "CACHE W", "CACHE R", "TOTAL", "EST $"):
            table.add_column(h)
        sessions: list[Session] = sorted(
            self.project.sessions, key=lambda s: s.usage.total, reverse=True
        )
        peak = max((s.usage.total for s in sessions), default=1) or 1
        for s in sessions:
            sid = s.file.rsplit("/", 1)[-1].replace(".jsonl", "")[:18]
            heat = s.usage.total / peak
            table.add_row(
                Text(sid, style="cyan"),
                num_cell(s.usage.input),
                num_cell(s.usage.output),
                num_cell(s.usage.cache_create),
                num_cell(s.usage.cache_read),
                num_cell(s.usage.total, heat=heat),
                cost_cell(s.cost),
            )
        table.focus()

    def action_dismiss(self) -> None:
        self.dismiss()


# ---------------------------------------------------------------------------
# DailyScreen — one row per day, one column per model family
# ---------------------------------------------------------------------------


class DailyScreen(Screen):
    """Daily token & cost view, per model family, cwd-scoped."""

    CSS = """
    #summary { dock: top; height: 1; padding: 0 1; background: $boost; }
    DataTable { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Static("scanning…", id="summary")
        table: DataTable = DataTable(zebra_stripes=True, id="daily-table")
        table.cursor_type = "row"
        yield table

    def on_mount(self) -> None:
        table = self.query_one("#daily-table", DataTable)
        table.add_column("DAY", key="day", width=12)
        for fam in FAMILIES:
            table.add_column(fam.upper(), key=fam)
        table.add_column("EST $", key="est")
        table.add_column("CLIENT $", key="client")

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
        if scope_cwd is not None:
            scope_label = f"cwd: {shorten(scope_cwd)}"
        else:
            scope_label = "global"

        margin_val = app._margin_label()

        if not days:
            if scope_cwd is not None:
                summary_text = Text.assemble(
                    (f"no transcripts for {shorten(scope_cwd)}", "dim"),
                    (" — press g for global", "dim"),
                )
            else:
                summary_text = Text("no transcripts found", "dim")
        else:
            summary_text = Text.assemble(
                (scope_label, "bold"),
                (f" · {len(days)} days", "dim"),
                ("   "),
                (f"{human(agg_tokens)} tok", "bold white"),
                ("   "),
                (f"${agg_cost:,.2f} est", "bold yellow"),
                ("   "),
                (f"${agg_cost * app._margin():,.2f} client", "green"),
                (f"   ·  margin:{margin_val}", "dim"),
            )
        self.query_one("#summary", Static).update(summary_text)

    def on_show(self) -> None:
        """Re-render when this screen becomes active."""
        self.refresh_daily()


# ---------------------------------------------------------------------------
# ProjectsScreen — existing leaderboard moved into a Screen
# ---------------------------------------------------------------------------


class ProjectsScreen(Screen):
    """Per-directory leaderboard (the original cctop view)."""

    CSS = """
    #summary { dock: top; height: 1; padding: 0 1; background: $boost; }
    #filter { dock: bottom; display: none; border: tall $accent; height: 3; }
    #filter.visible { display: block; }
    DataTable { height: 1fr; }
    """

    BINDINGS = [
        Binding("m", "toggle_merge", "Merge by cwd"),
        Binding("t", "sort('total')", "Sort total"),
        Binding("c", "sort('cost')", "Sort $"),
        Binding("o", "sort('output')", "Sort out"),
        Binding("n", "sort('project')", "Sort name"),
        Binding("slash", "filter", "Filter"),
        Binding("escape", "clear_filter", "Clear", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.merge_by_cwd = True
        self.sort_key = "total"
        self.filter_text = ""

    def compose(self) -> ComposeResult:
        yield Static("scanning…", id="summary")
        table: DataTable = DataTable(zebra_stripes=True, id="projects-table")
        table.cursor_type = "row"
        yield table
        yield Input(placeholder="filter projects…  (enter/esc to close)", id="filter")

    def on_mount(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        for key, header in COLUMNS:
            table.add_column(header, key=key)

    def _visible(self) -> list[Project]:
        app: CCTop = self.app  # type: ignore[assignment]
        rows = app.projects
        # Filter by scope_cwd when set
        if app.scope_cwd is not None:
            rows = [p for p in rows if p.key == app.scope_cwd]
        if self.filter_text:
            needle = self.filter_text.lower()
            rows = [p for p in rows if needle in shorten(p.key).lower()]
        rows = sorted(rows, key=SORTABLE[self.sort_key], reverse=self.sort_key != "project")
        return rows

    def refresh_table(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        table.clear()
        rows = self._visible()
        peak = max((p.usage.total for p in rows), default=1) or 1

        agg_total = 0
        agg_cost = 0.0
        agg_sessions = 0
        for p in rows:
            u = p.usage
            agg_total += u.total
            agg_cost += p.cost
            agg_sessions += u.sessions
            table.add_row(
                Text(shorten(p.key), style="cyan"),
                num_cell(u.sessions),
                num_cell(u.input),
                num_cell(u.output),
                num_cell(u.cache_create),
                num_cell(u.cache_read),
                num_cell(u.total, heat=u.total / peak),
                cost_cell(p.cost),
                key=p.key,
            )

        merge = "merged by cwd" if self.merge_by_cwd else "per folder"
        flt = f"  filter:'{self.filter_text}'" if self.filter_text else ""
        summary = Text.assemble(
            (f"{len(rows)} dirs", "bold"),
            (f" · {agg_sessions} sessions", "dim"),
            ("   "),
            (f"{human(agg_total)} tok", "bold white"),
            ("   "),
            (f"${agg_cost:,.2f} est", "bold yellow"),
            (f"   ·  sort:{self.sort_key} · {merge}{flt}", "dim"),
        )
        self.query_one("#summary", Static).update(summary)

    def on_show(self) -> None:
        """Re-render when this screen becomes active."""
        self.refresh_table()

    # ---- actions ------------------------------------------------------------

    def action_toggle_merge(self) -> None:
        self.merge_by_cwd = not self.merge_by_cwd
        app: CCTop = self.app  # type: ignore[assignment]
        app.load_data()

    def action_sort(self, key: str) -> None:
        self.sort_key = key
        self.refresh_table()

    def action_filter(self) -> None:
        flt = self.query_one("#filter", Input)
        flt.add_class("visible")
        flt.focus()

    def action_clear_filter(self) -> None:
        flt = self.query_one("#filter", Input)
        if flt.has_class("visible"):
            flt.value = ""
            self.filter_text = ""
            flt.remove_class("visible")
            self.query_one("#projects-table", DataTable).focus()
            self.refresh_table()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value
        self.refresh_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#filter", Input).remove_class("visible")
        self.query_one("#projects-table", DataTable).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        app: CCTop = self.app  # type: ignore[assignment]
        key = event.row_key.value
        proj = next((p for p in app.projects if p.key == key), None)
        if proj is not None:
            self.app.push_screen(SessionsScreen(proj))


# ---------------------------------------------------------------------------
# Root App — mode controller
# ---------------------------------------------------------------------------


class CCTop(App):
    """Claude Code token usage, top-style."""

    MODES = {
        "daily": DailyScreen,
        "projects": ProjectsScreen,
    }
    DEFAULT_MODE = "daily"

    CSS = """
    #drill { padding: 1 2; }
    #drill-title { height: 1; margin-bottom: 1; }
    #drill-table { height: 1fr; }
    #drill-hint { height: 1; margin-top: 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("g", "toggle_scope", "cwd/global"),
        Binding("d", "switch_mode('daily')", "Daily"),
        Binding("p", "switch_mode('projects')", "Projects"),
    ]

    def __init__(self, global_scope: bool = False) -> None:
        super().__init__()
        self.projects: list[Project] = []
        self.days: list[DayUsage] = []
        # scope_cwd: None → global; str → scoped to that cwd
        self.scope_cwd: str | None = None if global_scope else os.getcwd()

    def _margin(self) -> float:
        from cctop.data import MARGIN
        return MARGIN

    def _margin_label(self) -> str:
        m = self._margin()
        if m == 1.0:
            return "1.0 (unset)"
        return str(m)

    def on_mount(self) -> None:
        self.title = "cctop"
        self.sub_title = "Claude Code token usage"
        self.load_data()

    # ---- data loading (threaded) -------------------------------------------

    @work(thread=True, exclusive=True)
    def load_data(self) -> None:
        projects = scan(merge_by_cwd=True)
        # Use the projects-screen merge_by_cwd if available
        try:
            ps: ProjectsScreen = self.get_screen("projects")  # type: ignore[assignment]
            projects = scan(merge_by_cwd=ps.merge_by_cwd)
        except Exception:
            pass
        days = scan_daily(cwd=self.scope_cwd)
        self.call_from_thread(self._on_loaded, projects, days)

    def _on_loaded(self, projects: list[Project], days: list[DayUsage]) -> None:
        self.projects = projects
        self.days = days
        # Re-render whichever screen is currently active
        screen = self.screen
        if isinstance(screen, DailyScreen):
            screen.refresh_daily()
        elif isinstance(screen, ProjectsScreen):
            screen.refresh_table()

    # ---- actions ------------------------------------------------------------

    def action_refresh(self) -> None:
        # Update the active screen's summary to "scanning…"
        screen = self.screen
        try:
            screen.query_one("#summary", Static).update("scanning…")
        except Exception:
            pass
        self.load_data()

    def action_toggle_scope(self) -> None:
        """Flip scope_cwd between the launch cwd and None (global)."""
        if self.scope_cwd is None:
            self.scope_cwd = os.getcwd()
        else:
            self.scope_cwd = None
        self.load_data()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_scope(argv: list[str]) -> bool:
    """Return True if --global or -g is present in argv."""
    return "--global" in argv or "-g" in argv


def main() -> None:
    global_scope = parse_scope(sys.argv[1:])
    CCTop(global_scope=global_scope).run()


if __name__ == "__main__":
    main()
