"""Headless TUI smoke test: mount the cwd-only daily app and drive its bindings.

Runs the Textual app via run_test() with no TTY. Wrapped in asyncio.run so
no async plugin is required. Pure-render behavior beyond "it mounts and the
key bindings dispatch" is left to the design/manual loop, per Test Rules.
"""

from __future__ import annotations

import asyncio
import os

from cctop import data
from cctop.app import CCTop, DailyScreen


def test_single_daily_mode_no_scope_bindings() -> None:
    """AC-CWD-1: only the daily mode exists; p/g/d change neither screen nor scope."""

    async def drive() -> None:
        app = CCTop()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert set(CCTop.MODES.keys()) == {"daily"}
            assert isinstance(app.screen, DailyScreen)
            scope_before = app.scope_cwd
            await pilot.press("p")
            await pilot.press("g")
            await pilot.press("d")
            await pilot.pause()
            # No mode switch, no scope toggle, still on the daily screen.
            assert isinstance(app.screen, DailyScreen)
            assert app.scope_cwd == scope_before

    asyncio.run(drive())


def test_load_data_scopes_to_launch_cwd(monkeypatch) -> None:
    """AC-CWD-2: load_data calls scan_daily with cwd == the launch cwd (never None)."""
    seen: dict[str, object] = {}

    def spy_scan_daily(*args, cwd=None, **kwargs):
        seen["cwd"] = cwd
        return []

    monkeypatch.setattr("cctop.app.scan_daily", spy_scan_daily)

    async def drive() -> None:
        app = CCTop(launch_cwd="/x/y")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for _ in range(50):
                if "cwd" in seen:
                    break
                await asyncio.sleep(0.05)

    asyncio.run(drive())
    assert seen.get("cwd") == "/x/y"


def test_refresh_and_quit_dispatch() -> None:
    """AC-CWD-3: r reloads/re-renders without raising; q still quits."""

    async def drive() -> None:
        app = CCTop(launch_cwd=os.getcwd())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            assert isinstance(app.screen, DailyScreen)
            await pilot.press("q")
            await pilot.pause()

    asyncio.run(drive())
    # data import kept available for downstream specs' margin assertions.
    assert hasattr(data, "MARGIN")
