"""Session-wide leak checks with creator attribution across xdist workers."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from threading import Lock

import pytest


def system_roots():
    roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if sys.platform == "darwin":
        # macOS unistd.h: _CS_DARWIN_USER_TEMP_DIR. Python omits its symbolic
        # name, but accepts the confstr value used by getconf on the same host.
        roots.add(Path(os.confstr(65537)).resolve())
    return sorted(roots)


def entries(roots):
    return {str(path) for root in roots for path in root.glob("travel-mode-*")}


class TempGuard:
    def __init__(self, roots, before, ledger, worker):
        self.roots = {Path(root).resolve() for root in roots}
        self.before = set(before)
        self.ledger = Path(ledger)
        self.worker = worker
        self.test = "<session setup/teardown>"
        self.active = True
        self.lock = Lock()

    def audit(self, event, args):
        if not self.active or event not in {"tempfile.mkdtemp", "os.mkdir"}:
            return
        path = Path(os.fsdecode(args[0]))
        if not path.name.startswith("travel-mode-") or path.parent.resolve() not in self.roots:
            return
        row = dict(path=str(path.parent.resolve() / path.name), test=self.test, worker=self.worker)
        with self.lock, self.ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def leaks(self, *, own=False):
        owners = {}
        for ledger in self.ledger.parent.glob("*.jsonl"):
            if own and ledger != self.ledger:
                continue
            for line in ledger.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                owners[row["path"]] = row
        new = entries(self.roots) - self.before
        if own:
            new &= owners.keys()
        return [
            f"{path}: created by {owners[path]['test']} ({owners[path]['worker']})"
            if path in owners
            else f"{path}: creator unknown (outside instrumented test process)"
            for path in sorted(new)
        ]


def pytest_configure(config):
    if hasattr(config, "workerinput"):
        data = config.workerinput["travel_temp_guard"]
        worker = config.workerinput["workerid"]
    else:
        directory = tempfile.TemporaryDirectory(prefix="pytest-travel-temp-guard-")
        config._travel_temp_directory = directory
        roots = system_roots()
        data = dict(roots=list(map(str, roots)), before=sorted(entries(roots)), directory=directory.name)
        config._travel_temp_data = data
        worker = "controller"
    guard = TempGuard(data["roots"], data["before"], Path(data["directory"]) / f"{worker}.jsonl", worker)
    config._travel_temp_guard = guard
    sys.addaudithook(guard.audit)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    node.workerinput["travel_temp_guard"] = node.config._travel_temp_data


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item):
    guard = item.config._travel_temp_guard
    guard.test = item.nodeid
    try:
        yield
    finally:
        guard.test = "<session setup/teardown>"


@pytest.fixture(scope="session", autouse=True)
def no_system_temp_leaks(request):
    yield
    guard = request.config._travel_temp_guard
    leaks = guard.leaks(own=hasattr(request.config, "workerinput"))
    assert not leaks, "New travel-mode temporary entries:\n" + "\n".join(leaks)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    config = session.config
    guard = config._travel_temp_guard
    if not hasattr(config, "workerinput"):
        # All workers have exited. Never blame a concurrently running test for
        # another worker's temporary, still-live directory.
        leaks = guard.leaks()
        if leaks:
            config.pluginmanager.get_plugin("terminalreporter").write_sep(
                "!", "New travel-mode temporary entries:\n" + "\n".join(leaks), red=True
            )
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
    guard.active = False
    if hasattr(config, "_travel_temp_directory"):
        config._travel_temp_directory.cleanup()
