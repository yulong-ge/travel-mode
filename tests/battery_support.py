"""Synthetic fresh generations for tests of the voltage/current fallback."""

from travel_mode.system import battery


class FakeClock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


def fresh_readings(monkeypatch, value=None):
    original = battery.read
    count = 1000

    def read(**kwargs):
        nonlocal count
        if kwargs.get("endpoint"):
            return original(**kwargs)
        result = original(**kwargs) if value is None else value() if callable(value) else value
        if result["available"]:
            count += 1
            return {**result, "update_time": count}
        return result

    monkeypatch.setattr(battery, "read", read)
