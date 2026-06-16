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
    # Simulate the env value already baked into the module at import (data.MARGIN is
    # read from CCTOP_MARGIN at import time, so a late setenv would NOT affect it — set
    # the module global directly). monkeypatch restores the true original at teardown (A6).
    monkeypatch.setattr(data, "MARGIN", 1.3)

    # Write a .cctop file in tmp_path specifying margin 3.0 — it must win over env 1.3.
    dotcctop = tmp_path / ".cctop"
    dotcctop.write_text(json.dumps({"margin": 3.0}))

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


# ---------------------------------------------------------------------------
# AC-CSV-1..4 — multi-select day rows + CSV billing export
# ---------------------------------------------------------------------------


def _day(day: str, **fam_usages):
    """Build a DayUsage with the given family -> Usage map."""
    from cctop.data import DayUsage

    return DayUsage(day=day, by_model=dict(fam_usages))


def test_daily_csv_shape_and_totals(monkeypatch) -> None:
    """AC-CSV-1: daily_csv emits the D4 header, the data row, and a TOTAL row matching it."""
    from cctop.app import daily_csv
    from cctop.data import Usage

    monkeypatch.setattr(data, "MARGIN", 1.0)  # client == est for this AC

    # Opus-only usage costing exactly 6.72 est: 1_344_000 input × $5/MTok = 6.72.
    u = Usage(input=1_344_000)
    assert data.cost_of(u, "opus") == pytest.approx(6.72)
    days = [_day("2026-06-16", opus=u)]

    out = daily_csv(days)
    lines = out.strip().split("\n")

    header = (
        "day,haiku_tokens,haiku_cost,sonnet_tokens,sonnet_cost,"
        "opus_tokens,opus_cost,fable_tokens,fable_cost,est_usd,client_usd"
    )
    assert lines[0] == header
    assert lines[1] == "2026-06-16,0,0.00,0,0.00,1344000,6.72,0,0.00,6.72,6.72"
    # TOTAL row's numeric columns equal the single data row.
    assert lines[2] == "TOTAL,0,0.00,0,0.00,1344000,6.72,0,0.00,6.72,6.72"


def test_daily_csv_raw_numbers_and_missing_family(monkeypatch) -> None:
    """AC-CSV-2: numbers are raw (no $/k) and a day missing a family renders 0/0.00, no raise."""
    from cctop.app import daily_csv
    from cctop.data import Usage

    monkeypatch.setattr(data, "MARGIN", 1.0)

    # Sonnet-only day: 298000 input tokens — opus/haiku/fable absent (the None-guard path).
    u = Usage(input=298_000)
    days = [_day("2026-06-16", sonnet=u)]

    out = daily_csv(days)  # must not raise on the missing families
    assert "298000" in out          # raw integer, not "298k"
    assert "298k" not in out
    assert "$" not in out           # no dollar signs
    data_row = out.strip().split("\n")[1]
    cols = data_row.split(",")
    # day,haiku_tok,haiku_cost,sonnet_tok,sonnet_cost,opus_tok,opus_cost,fable_tok,fable_cost,...
    assert cols[3] == "298000"      # sonnet_tokens raw
    assert cols[1] == "0" and cols[2] == "0.00"   # haiku missing → 0 / 0.00
    assert cols[5] == "0" and cols[6] == "0.00"   # opus missing → 0 / 0.00
    assert cols[7] == "0" and cols[8] == "0.00"   # fable missing → 0 / 0.00


def _drive_days(monkeypatch, days):
    """Mount CCTop with app.days forced to `days` via a stubbed scan_daily."""
    monkeypatch.setattr("cctop.app.scan_daily", lambda *a, **k: days)


def test_space_toggles_selection(monkeypatch) -> None:
    """AC-CSV-3: space toggles the cursor day in/out of DailyScreen.selected."""
    from cctop.data import Usage

    monkeypatch.setattr(data, "MARGIN", 1.0)
    days = [_day("2026-06-16", opus=Usage(input=1_000_000))]
    _drive_days(monkeypatch, days)

    async def drive() -> None:
        app = CCTop(launch_cwd="/x/y")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for _ in range(50):
                if app.days:
                    break
                await asyncio.sleep(0.05)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, DailyScreen)
            await pilot.press("space")
            await pilot.pause()
            assert screen.selected == {"2026-06-16"}
            await pilot.press("space")
            await pilot.pause()
            assert screen.selected == set()

    asyncio.run(drive())


def test_copy_csv_marked_vs_all(monkeypatch) -> None:
    """AC-CSV-4: y copies daily_csv(marked) when any marked, else daily_csv(all visible)."""
    from cctop.app import daily_csv
    from cctop.data import Usage

    monkeypatch.setattr(data, "MARGIN", 1.0)
    days = [
        _day("2026-06-16", opus=Usage(input=1_000_000)),
        _day("2026-06-15", sonnet=Usage(input=500_000)),
    ]
    _drive_days(monkeypatch, days)

    async def drive() -> None:
        app = CCTop(launch_cwd="/x/y")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for _ in range(50):
                if app.days:
                    break
                await asyncio.sleep(0.05)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, DailyScreen)

            clip = MagicMock()
            monkeypatch.setattr(app, "copy_to_clipboard", clip)

            # Nothing marked → copies all visible days.
            await pilot.press("y")
            await pilot.pause()
            clip.assert_called_once_with(daily_csv(days))

            # Mark the cursor (first) day, then copy → only that day.
            clip.reset_mock()
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            marked = [d for d in days if d.day in screen.selected]
            clip.assert_called_once_with(daily_csv(marked))

    asyncio.run(drive())
