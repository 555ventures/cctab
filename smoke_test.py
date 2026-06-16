"""Headless smoke test: mount the app, exercise sort/merge/filter/drill, no TTY."""

import asyncio

from cctab.app import CCTab
from cctab.data import scan


async def main() -> None:
    # data layer
    projects = scan(merge_by_cwd=True)
    print(f"data.scan -> {len(projects)} projects")
    if projects:
        top = projects[0]
        print(f"  top: {top.key}  total={top.usage.total}  sessions={top.usage.sessions}")

    # TUI layer, driven headlessly
    app = CCTab()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # let the threaded scan finish
        for _ in range(50):
            if app.projects:
                break
            await asyncio.sleep(0.1)
        assert app.projects, "no projects loaded into app"
        await pilot.press("c")  # sort by cost
        await pilot.press("m")  # toggle merge
        await asyncio.sleep(0.3)
        await pilot.press("t")  # sort by total
        # drill into first row
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        print("TUI smoke: OK")


if __name__ == "__main__":
    asyncio.run(main())
