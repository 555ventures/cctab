"""Headless TUI smoke test: mount the cwd-only daily app and drive its bindings.

Runs the Textual app via run_test() with no TTY. Wrapped in asyncio.run so
no async plugin is required. Pure-render behavior beyond "it mounts and the
key bindings dispatch" is left to the design/manual loop, per Test Rules.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


# ---------------------------------------------------------------------------
# AC-CFG-5, AC-CFG-6 — per-directory margin config (.cctop)
# ---------------------------------------------------------------------------


def test_launch_reads_dotcctop_margin(tmp_path: Path, monkeypatch) -> None:
    """AC-CFG-5: CCTop(launch_cwd=D) with D/.cctop {"margin": 3.0} sets active margin to 3.0.

    .cctop takes precedence over CCTOP_MARGIN env — client_cost(10.0) must equal 30.0.
    Restores data.MARGIN via monkeypatch to prevent module-global leakage (A6).
    """
    # Snapshot the original MARGIN so monkeypatch can restore it after the test.
    orig_margin = data.MARGIN
    monkeypatch.setattr(data, "MARGIN", orig_margin)

    # Write a .cctop file in tmp_path specifying margin 3.0.
    dotcctop = tmp_path / ".cctop"
    dotcctop.write_text(json.dumps({"margin": 3.0}))

    # Simulate CCTOP_MARGIN=1.3 being set in the environment — .cctop must win.
    monkeypatch.setenv("CCTOP_MARGIN", "1.3")

    async def drive() -> None:
        app = CCTop(launch_cwd=str(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Active margin must be 3.0, read from .cctop, overriding env 1.3.
            assert data.client_cost(10.0) == pytest.approx(30.0), (
                f"expected client_cost(10.0)==30.0 but got {data.client_cost(10.0)}; "
                f"data.MARGIN={data.MARGIN}"
            )

    asyncio.run(drive())


def test_edit_margin_sets_and_writes(tmp_path: Path, monkeypatch) -> None:
    """AC-CFG-6: pressing e, typing 2.5, and submitting sets active margin to 2.5 and writes .cctop.

    client_cost(10.0) must equal 25.0 after submit. write_dir_margin must be called with
    (launch_cwd, 2.5). Restores data.MARGIN via monkeypatch to prevent leakage (A6).
    """
    orig_margin = data.MARGIN
    monkeypatch.setattr(data, "MARGIN", orig_margin)

    # Patch write_dir_margin so we can verify it is called without touching disk.
    write_spy = MagicMock(return_value=True)
    monkeypatch.setattr(data, "write_dir_margin", write_spy)

    async def drive() -> None:
        app = CCTop(launch_cwd=str(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Press e to reveal the margin input.
            await pilot.press("e")
            await pilot.pause()
            # Type the new margin value and submit.
            await pilot.press(*list("2.5"))
            await pilot.press("enter")
            await pilot.pause()
            # Active margin must now be 2.5.
            assert data.client_cost(10.0) == pytest.approx(25.0), (
                f"expected client_cost(10.0)==25.0 but got {data.client_cost(10.0)}; "
                f"data.MARGIN={data.MARGIN}"
            )

    asyncio.run(drive())

    # write_dir_margin must have been called with the launch cwd and 2.5.
    write_spy.assert_called_once_with(str(tmp_path), pytest.approx(2.5))


def test_edit_margin_invalid_no_change(tmp_path: Path, monkeypatch) -> None:
    """AC-CFG-6: invalid submitted values (abc, -1, inf) leave margin unchanged and write nothing.

    Restores data.MARGIN via monkeypatch to prevent leakage (A6).
    """
    orig_margin = data.MARGIN
    monkeypatch.setattr(data, "MARGIN", orig_margin)

    write_spy = MagicMock(return_value=True)
    monkeypatch.setattr(data, "write_dir_margin", write_spy)

    invalid_inputs = ["abc", "-1", "inf"]

    for bad_value in invalid_inputs:
        # Reset MARGIN to orig before each sub-test.
        monkeypatch.setattr(data, "MARGIN", orig_margin)
        margin_before = data.MARGIN

        async def drive(value: str = bad_value) -> None:
            app = CCTop(launch_cwd=str(tmp_path))
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await pilot.press("e")
                await pilot.pause()
                await pilot.press(*list(value))
                await pilot.press("enter")
                await pilot.pause()

        asyncio.run(drive())

        # Margin must be unchanged for invalid input.
        assert data.MARGIN == pytest.approx(margin_before), (
            f"expected MARGIN={margin_before} after invalid input '{bad_value}' "
            f"but got {data.MARGIN}"
        )

    # write_dir_margin must never have been called for any invalid input.
    write_spy.assert_not_called()
