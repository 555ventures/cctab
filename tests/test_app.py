"""Headless TUI smoke test: mount the app, drive sort/merge/filter/drill.

Runs the Textual app via run_test() with no TTY. Wrapped in asyncio.run so
no async plugin is required. Pure-render behavior beyond "it mounts and the
key bindings dispatch" is left to the design/manual loop, per Test Rules.
"""

from __future__ import annotations

import asyncio
import os

from cctop.app import CCTop


def test_app_mounts_and_handles_keys() -> None:
    """AC-APP-1: app mounts, enters projects mode, sort/merge/filter bindings fire."""

    async def drive() -> None:
        app = CCTop()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for _ in range(50):
                if app.projects:
                    break
                await asyncio.sleep(0.05)
            # Must enter projects mode first — leaderboard bindings live on ProjectsScreen
            await pilot.press("p")
            await pilot.pause()
            await pilot.press("c")  # sort by cost
            await pilot.press("m")  # toggle merge
            await pilot.press("t")  # sort by total
            await pilot.press("slash")  # open filter
            await pilot.press("escape")  # clear filter
            await pilot.pause()

    asyncio.run(drive())


def test_app_starts_in_daily_mode() -> None:
    """AC-APP-2: app mounts with no flag, starts in daily mode with cwd scope."""

    async def drive() -> None:
        app = CCTop()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # App must start in "daily" mode (DEFAULT_MODE)
            assert app.current_mode == "daily"
            # scope_cwd must be set (not None) when launched without --global
            assert app.scope_cwd is not None
            # A DailyScreen must be the current screen
            from cctop.app import DailyScreen
            assert isinstance(app.screen, DailyScreen)

    asyncio.run(drive())


def test_parse_scope_and_app_scope() -> None:
    """AC-APP-3: parse_scope maps argv flags; CCTop(global_scope=) sets scope_cwd."""
    from cctop.app import parse_scope

    # parse_scope returns True for --global and -g, False for empty
    assert parse_scope(["--global"]) is True
    assert parse_scope(["-g"]) is True
    assert parse_scope([]) is False

    async def drive_global() -> None:
        app = CCTop(global_scope=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.scope_cwd is None

    async def drive_local() -> None:
        app = CCTop(global_scope=False)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.scope_cwd == os.getcwd()

    asyncio.run(drive_global())
    asyncio.run(drive_local())


def test_app_mode_and_scope_bindings() -> None:
    """AC-APP-4: d/p/g switch modes and toggle scope; leaderboard bindings work in projects mode."""

    async def drive() -> None:
        app = CCTop()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Start in daily mode
            assert app.current_mode == "daily"

            # Press 'p' to switch to projects mode
            await pilot.press("p")
            await pilot.pause()
            assert app.current_mode == "projects"

            # In projects mode, leaderboard bindings should dispatch without error
            await pilot.press("c")   # sort by cost
            await pilot.press("m")   # toggle merge
            await pilot.press("t")   # sort by total
            await pilot.press("slash")   # open filter
            await pilot.press("escape")  # clear filter
            await pilot.pause()

            # Press 'd' to switch back to daily mode
            await pilot.press("d")
            await pilot.pause()
            assert app.current_mode == "daily"

            # Press 'g' to toggle scope — scope_cwd should flip
            scope_before = app.scope_cwd
            await pilot.press("g")
            await pilot.pause()
            assert app.scope_cwd != scope_before

    asyncio.run(drive())
