"""The demo harness admits only the new exact read-only health command forms."""

import pytest
from e2e_guard import GuardViolation

from travel_mode.system import health, shell


def test_guarded_demo_health_observations_remain_hermetic(sandbox):
    _, guard, machine = sandbox
    shell.set_runner(guard)
    report = health.collect(clock=lambda: 0)
    assert len(report["checks"]) == 9
    assert not any(row["status"] == "unknown" for row in report["checks"])
    assert not guard.tripped
    assert shell.run_rc(health.native_command("brightness")) == ("0.5", 0)
    assert shell.run_rc(["ioreg", "-rn", "AppleSmartBattery"])[1] == 0
    assert machine.calls


def test_guard_refuses_other_python_modules_and_native_operations(sandbox):
    _, guard, machine = sandbox
    with pytest.raises(GuardViolation):
        guard(health.native_command("set-brightness"))
    command = health.native_command("displays")
    command[2] = "arbitrary.module"
    with pytest.raises(GuardViolation):
        guard(command)
    assert machine.calls == []
