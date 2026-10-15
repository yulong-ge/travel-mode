"""The TUI snapshot must include configured fixture/application discovery roots."""


async def test_tui_snapshot_honors_extra_app_dirs(monkeypatch, tmp_path):
    from travel_mode.system.app_snapshot import AppSnapshot
    from travel_mode.tui.app import TravelApp

    extra = str(tmp_path / "extra")
    monkeypatch.setenv("TRAVEL_MODE_APP_DIRS", extra)
    observed = []
    capture = AppSnapshot.capture

    def record(directories):
        observed.append(directories)
        return capture(directories)

    monkeypatch.setattr(AppSnapshot, "capture", record)
    app = TravelApp(scenario="away")
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert observed and all(extra in dirs for dirs in observed)
