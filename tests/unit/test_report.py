from travel_mode.flows.report import parse_darkwake_reasons, parse_sleep_segments

LOG = """2026-01-01 01:00:00 Using Batt: Charge: 90; Entering Sleep
2026-01-01 02:00:00 Using Batt: Charge: 87; Clamshell Sleep
2026-01-01 03:00:00 Using Batt: Charge: 87; Assertions Summary
2026-01-01 04:00:00 DarkWake due to RTC/Maintenance BATT
2026-01-01 05:00:00 DarkWake due to TCPKeepAlive BATT
"""


def test_parse_sleep_segments():
    assert parse_sleep_segments(LOG)[0]["drain"] == 3


def test_parse_darkwake_reasons():
    assert parse_darkwake_reasons(LOG) == {"RTC": 1, "TCPKeepAlive": 1}
