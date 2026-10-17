"""The new compiled fixture is source-bound and never executed on the host in tests."""

import pytest
from e2e_guard import GuardViolation
from e2e_local import product_environment
from e2e_refusal import SOURCE, compile_command, create
from e2e_ui import InteractiveHarness, answers, select_answer

from travel_mode import cli
from travel_mode.i18n import set_language


def test_refusing_fixture_guard_binds_source_executable_and_bundle(sandbox):
    harness, guard, machine = sandbox
    create(harness.root, guard)
    assert ".terminateCancel" in (harness.root / "Refusing.swift").read_text()
    assert tuple(compile_command(harness.root)) in machine.calls
    (harness.root / "Refusing.swift").write_text(SOURCE + "// modified")
    with pytest.raises(GuardViolation):
        guard(compile_command(harness.root))
    with pytest.raises(GuardViolation):
        guard(["swiftc", "/tmp/other.swift", "-o", str(guard.refusing / "Contents/MacOS/refusing")])
    (harness.root / "Refusing.swift").unlink()
    with pytest.raises(GuardViolation):
        guard(compile_command(harness.root))


@pytest.mark.parametrize("scenario", ["work", "away"])
def test_ui_harness_refusing_fixture_real_product_cli_path(sandbox, scenario, monkeypatch):
    local, guard, _machine = sandbox
    harness = InteractiveHarness(local.root, guard, local.real_state, pause=lambda _: None)
    harness.scenario = scenario
    try:
        with product_environment(harness.root, guard):
            set_language("en")
            harness.setup()
            assert harness.refusing_ready
            policy = answers(scenario)
            monkeypatch.setattr("builtins.input", lambda prompt: select_answer(prompt, policy)[1])
            assert cli.main(["--no-tui", "--no-measure", "--lang", "en"]) == (1 if scenario == "work" else 0)
            harness.assert_on()
            assert cli.main(["off"]) == 0
            harness.assert_off()
            assert not guard.tripped
    finally:
        with product_environment(harness.root, guard):
            harness.cleanup()
