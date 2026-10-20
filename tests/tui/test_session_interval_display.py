"""Session rendering and worker errors separate telemetry from program faults."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from textual.widgets import Static

from session_interval_support import interval_case
from travel_mode import failures, sessions
from travel_mode.i18n import set_language, t
from travel_mode.system import battery
from travel_mode.tui.app import TravelApp


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("case", ["stale", "unchanged", "reset", "normal"])
async def test_battery_refresh_and_session_render_never_log_interval_errors(case, language, monkeypatch):
    set_language(language)
    start, _, reading = interval_case(case)
    app = TravelApp(scenario="away", measure=True)
    async with app.run_test(size=(120, 45)):
        await app.wait_for_idle()
        monkeypatch.setattr(sessions, "observe", lambda: start)
        sessions.begin(app.st)
        app.save()
        app.view_mode = "session"
        app.update_primary()
        counts = dict(app.st["session"]["actions"])
        monkeypatch.setattr(battery, "read", lambda **kwargs: reading)
        worker = app.refresh_battery()
        await app.wait_for_idle()
        await worker.wait()
        generation = app._refresh_generation
        assert await asyncio.wait_for(app.screen.wait_for_refresh(), 30)
        assert app.refresh_complete and generation == app._refresh_generation
        text = str(app.query_one("#session-summary", Static).content)
        if case == "normal":
            assert app.session_view["average_watts"] == pytest.approx(14639022 / 27508 / 1000)
        else:
            key = "battery.interval_discontinuous" if case == "reset" else "battery.interval_waiting"
            assert t(key) in text
            assert app.session_view["average_watts"] is None
        assert app.is_running and app._exception is None
        assert app.st["session"]["actions"] == counts
        assert not app.st.get("action_failures") and not failures.log_path().exists()
        assert "travel-mode.log" not in str(app.query_one("#last-result", Static).content)
        # A cached reading cannot bypass the shared sleep-aware source while
        # the current estimate is still unavailable.
        app.current_estimate = None
        app.update_session()
        assert app.session_view["remaining_minutes"] is None
        # A cached/inflight snapshot must also be rejected on a plan refresh or
        # completion-button redraw, even without the hardware timestamp.
        app.current_battery = {**reading, "update_time": None}
        app.current_battery_at = datetime.fromisoformat(start["at"]) - timedelta(seconds=1)
        app.update_session()
        assert await asyncio.wait_for(app.screen.wait_for_refresh(), 30)
        assert t("battery.interval_waiting") in str(app.query_one("#session-summary", Static).content)
        assert not failures.log_path().exists()


@pytest.mark.parametrize("reason", ["counter_reset", "source_changed", "missing_update_marker"])
async def test_interval_worker_errors_are_not_internal_but_program_errors_keep_traceback(reason):
    app = TravelApp(scenario="away", measure=False)
    async with app.run_test(size=(120, 45)):
        await app.wait_for_idle()

        async def unavailable():
            raise battery.IntervalError(reason)

        worker = app.run_worker(unavailable(), group="fixture-interval")
        await app.wait_for_idle()
        await asyncio.wait_for(app.worker_error_reported(worker).wait(), 30)
        assert await asyncio.wait_for(app.screen.wait_for_refresh(), 30)
        assert battery.describe_interval(reason) in str(app.query_one("#last-result", Static).content)
        assert not failures.log_path().exists() and not app.st.get("action_failures")

        async def bug():
            raise LookupError("fixture session invariant")

        worker = app.run_worker(bug(), group="fixture-bug")
        await app.wait_for_idle()
        await asyncio.wait_for(app.worker_error_reported(worker).wait(), 30)
        assert await asyncio.wait_for(app.screen.wait_for_refresh(), 30)
        rows = [json.loads(line) for line in failures.log_path().read_text().splitlines()]
        assert len(rows) == 1 and rows[0]["kind"] == "internal"
        assert "LookupError" in rows[0]["traceback"]
        assert "travel-mode.log" in str(app.query_one("#last-result", Static).content)
        assert app.is_running and app._exception is None
