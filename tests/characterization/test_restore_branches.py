"""Recovery command contracts for previously unrecorded restore branches."""

import plistlib

import pytest

from travel_mode import state
from travel_mode.flows.restore import run_restore
from travel_mode.system.process_control import process_table

from .conftest import artifact, assert_golden

pytestmark = pytest.mark.usefixtures("serial_batches")


@pytest.mark.parametrize("case", ["reload", "loaded", "invalid", "power", "pids", "reopen-order"])
def test_restore_branches(case, machine, capsys):
    st = state.new_state()
    if case in {"reload", "loaded", "invalid"}:
        st["unloaded_svcs"] = [str(machine["plist"])]
        st["unloaded_sys_svcs"] = [str(machine["daemon"])]
        if case == "reload":
            # The fixture's loaded set is observed through this explicit response.
            from travel_mode.system.shell import set_runner

            fake = machine["fake"]
            from travel_mode.system.shell import CmdResult

            fake.responses[("launchctl", "list")] = CmdResult("PID Status Label\n", "", 0)
            fake.responses[("sudo", "-n", "launchctl", "list")] = CmdResult("PID Status Label\n", "", 0)
            set_runner(fake)
        elif case == "invalid":
            machine["plist"].unlink()
            machine["daemon"].write_bytes(plistlib.dumps({"Label": 123}))
    elif case == "power":
        st.update(batt_prof="displaysleep 7 sleep 12 powernap 1", ac_prof="sleep 22 womp 1")
    elif case == "pids":
        path = machine["paths"]["Freeze Demo"]
        machine["processes"]["910002"][0] = "T"
        machine["processes"]["910099"] = ["T", path + "/Contents/Helpers/helper"]
        st["frozen_apps"] = [path]
        st["frozen_pids"] = [row.receipt(path) for row in process_table() if row.pid in {910002, 910099}]
    else:
        st["quit_apps"] = [machine["paths"][name] for name in ("Quit Demo", "Ask Demo", "Network Demo")]
    state.save_state(st)
    machine["fake"].calls.clear()
    messages = []
    result = run_restore(st, lambda kind, msg: messages.append([kind, msg]))
    assert (
        result
        == {
            "reload": (2, 0),
            "loaded": (2, 0),
            "invalid": (0, 2),
            "power": (2, 0),
            "pids": (1, 0),
            "reopen-order": (3, 0),
        }[case]
    )
    if case == "invalid":
        assert len(st["action_failures"]) == 2
        assert {r["kind"] for r in st["action_failures"]} == {"action"}
        assert st["unloaded_svcs"] and st["unloaded_sys_svcs"]
    value = artifact(machine, int(bool(result[1])))
    assert_golden("restore-" + case, value, machine["root"])
